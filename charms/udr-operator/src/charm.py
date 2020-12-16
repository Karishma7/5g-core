#!/usr/bin/env python3
# Copyright 2020 Tata Elxsi
#
# Licensed under the Apache License, Version 2.0 (the License); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an AS IS BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#
# For those usages not covered by the Apache License, Version 2.0 please
# contact: canonical@tataelxsi.onmicrosoft.com
#
# To get in touch with the maintainers, please contact:
# canonical@tataelxsi.onmicrosoft.com
##
""" Defining udr charm events """

import logging
from typing import Any, Dict, NoReturn
from ops.charm import CharmBase, CharmEvents
from ops.main import main
from ops.framework import StoredState, EventBase, EventSource
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus

from oci_image import OCIImageResource, OCIImageResourceError

from pod_spec import make_pod_spec


logger = logging.getLogger(__name__)


class ConfigurePodEvent(EventBase):
    """Configure Pod event"""


class UdrEvents(CharmEvents):
    """UDR Events"""

    configure_pod = EventSource(ConfigurePodEvent)


class UdrCharm(CharmBase):
    """ UDR charm events class definition """

    state = StoredState()
    on = UdrEvents()

    def __init__(self, *args):
        super().__init__(*args)
        self.state.set_default(pod_spec=None)

        self.image = OCIImageResource(self, "image")

        # Registering regular events
        self.framework.observe(self.on.start, self.configure_pod)
        self.framework.observe(self.on.config_changed, self.configure_pod)
        self.framework.observe(self.on.upgrade_charm, self.configure_pod)
        self.framework.observe(self.on.leader_elected, self.configure_pod)
        self.framework.observe(self.on.update_status, self.configure_pod)

        # Registering custom internal events
        self.framework.observe(self.on.configure_pod, self.configure_pod)

        # Registering required relation changed events
        self.framework.observe(
            self.on.mongodb_relation_changed, self._on_mongodb_relation_changed
        )

        # Registering required relation departed events
        self.framework.observe(
            self.on.mongodb_relation_departed, self._on_mongodb_relation_departed
        )

        # Registering required relation changed events
        self.framework.observe(
            self.on.nrf_relation_changed, self._on_nrf_relation_changed
        )

        # Registering required relation departed events
        self.framework.observe(
            self.on.nrf_relation_departed, self._on_nrf_relation_departed
        )

        # -- initialize states --
        self.state.set_default(mongodb_host=None)
        self.state.set_default(nrf_host=None)
        self.state.set_default(mongodb_uri=None)

    def _on_mongodb_relation_changed(self, event: EventBase) -> NoReturn:
        """Reads information about the MongoDB relation.

        Args:
           event (EventBase): MongoDB relation event.
        """
        if event.app not in event.relation.data:
            return
        # data_loc = event.unit if event.unit else event.app

        mongodb_host = event.relation.data[event.app].get("hostname")
        mongodb_uri = event.relation.data[event.app].get("mongodb_uri")
        logging.info("UDM Requires MongoDB")
        logging.info(mongodb_host)
        logging.info(mongodb_uri)
        if (
            mongodb_host  # noqa
            and mongodb_uri  # noqa
            and (
                self.state.mongodb_host != mongodb_host
                or self.state.mongodb_uri != mongodb_uri
            )  # noqa
        ):
            self.state.mongodb_host = mongodb_host
            self.state.mongodb_uri = mongodb_uri
            self.on.configure_pod.emit()

    def _on_mongodb_relation_departed(self, event: EventBase) -> NoReturn:
        """Clears data from MongoDB relation.

        Args:
            event (EventBase): MongoDB relation event.
        """
        logging.info(event)
        self.state.mongodb_host = None
        self.state.mongodb_uri = None
        self.on.configure_pod.emit()

    def _on_nrf_relation_changed(self, event: EventBase) -> NoReturn:
        """Reads information about the NRF relation.

        Args:
           event (EventBase): NRF relation event.
        """
        if event.app not in event.relation.data:
            return
        # data_loc = event.unit if event.unit else event.app

        nrf_host = event.relation.data[event.app].get("hostname")
        logging.info("UDM Requires From NRF")
        logging.info(nrf_host)
        if nrf_host and self.state.nrf_host != nrf_host:
            self.state.nrf_host = nrf_host
            self.on.configure_pod.emit()

    def _on_nrf_relation_departed(self, event: EventBase) -> NoReturn:
        """Clears data from NRF relation.

        Args:
            event (EventBase): NRF relation event.
        """
        logging.info(event)
        self.state.nrf_host = None
        self.on.configure_pod.emit()

    def _missing_relations(self) -> str:
        """Checks if there missing relations.

        Returns:
            str: string with missing relations
        """
        data_status = {"nrf": self.state.nrf_host, "mongodb": self.state.mongodb_uri}
        missing_relations = [k for k, v in data_status.items() if not v]
        return ", ".join(missing_relations)

    @property
    def relation_state(self) -> Dict[str, Any]:
        """Collects relation state configuration for pod spec assembly.

        Returns:
            Dict[str, Any]: relation state information.
        """
        relation_state = {
            "nrf_host": self.state.nrf_host,
            "mongodb_host": self.state.mongodb_host,
            "mongodb_uri": self.state.mongodb_uri,
        }  # noqa

        return relation_state

    def configure_pod(self, event: EventBase) -> NoReturn:
        """Assemble the pod spec and apply it, if possible.
        Args:
            event (EventBase): Hook or Relation event that started the
                               function.
        """
        logging.info(event)
        missing = self._missing_relations()
        if missing:
            status = "Waiting for {0} relation{1}"
            self.unit.status = BlockedStatus(
                status.format(missing, "s" if "," in missing else "")
            )
            return
        if not self.unit.is_leader():
            self.unit.status = ActiveStatus("ready")
            return

        self.unit.status = MaintenanceStatus("Assembling pod spec")

        # Fetch image information
        try:
            self.unit.status = MaintenanceStatus("Fetching image information")
            image_info = self.image.fetch()
        except OCIImageResourceError:
            self.unit.status = BlockedStatus("Error fetching image information")
            return

        try:
            pod_spec = make_pod_spec(
                image_info,
                self.model.config,
                self.relation_state,
                self.model.app.name,
            )
        except ValueError as exc:
            logger.exception("Config/Relation data validation error")
            self.unit.status = BlockedStatus(str(exc))
            return

        if self.state.pod_spec != pod_spec:
            self.model.pod.set_spec(pod_spec)
            self.state.pod_spec = pod_spec

        self.unit.status = ActiveStatus("ready")


if __name__ == "__main__":
    main(UdrCharm)
