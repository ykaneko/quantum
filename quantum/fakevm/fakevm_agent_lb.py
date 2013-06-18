# vim: tabstop=4 shiftwidth=4 softtabstop=4
# Copyright 2013 Isaku Yamahata <yamahata at private email ne jp>
#                               <yamahata at valinux co jp>
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
# @author: Isaku Yamahata
# @author: Yoshihiro Kaneko

import time

from oslo.config import cfg

from quantum.agent.linux import ip_lib
from quantum.agent.linux import utils
from quantum.common import utils as q_utils
from quantum.extensions import portbindings
from quantum.fakevm import fakevm_agent_plugin_base
from quantum.plugins.linuxbridge.common import config


class QuantumFakeVMAgentLB(
        fakevm_agent_plugin_base.QuantumFakeVMAgentPluginBase):
    _BRIDGE_PREFIX = 'qfbr-'
    _PORT_PREFIX = 'qfp-'

    def __init__(self):
        super(QuantumFakeVMAgentLB, self).__init__()
        self.conf = None
        self.root_helper = None
        self.interface_mappings = q_utils.parse_mappings(
            cfg.CONF.LINUX_BRIDGE.physical_interface_mappings)

    def init(self, conf):
        super(QuantumFakeVMAgentLB, self).init(conf)
        self.vif_type = portbindings.VIF_TYPE_BRIDGE
        if self.conf.FAKEVM.allow_multi_node_emulate:
            self._init_bridge()

    def cleanup(self):
        self._cleanup_bridge()

    def _get_vif_bridge_name(self, network_id, vif_uuid):
        return 'brq' + network_id[0:11]

    def _get_tap_name(self, device):
        return 'tap' + device[0:11]

    def _get_hub_name(self, device):
        return (self._BRIDGE_PREFIX + device)[:self.DEV_NAME_LEN]

    def _get_port_name(self, device):
        return (self._PORT_PREFIX + device)[:self.DEV_NAME_LEN]

    def _execute(self, command):
        return utils.execute(command, root_helper=self.root_helper)

    def _bridge_exists(self, bridge_name):
        try:
            self._execute(['brctl', 'show', bridge_name])
        except RuntimeError:
            return False
        return True

    def _ensure_bridge(self, bridge_name):
        if not self._bridge_exists(bridge_name):
            if self._execute(['brctl', 'addbr', bridge_name]):
                raise RuntimeError('brctl addbr %s failed' % bridge_name)
            if self._execute(['brctl', 'setfd', bridge_name, str(0)]):
                raise RuntimeError('brctl setfd %s 0 failed' % bridge_name)
            if self._execute(['brctl', 'stp', bridge_name, 'off']):
                raise RuntimeError('brctl stp %s off failed' % bridge_name)
            if self._execute(['ip', 'link', 'set', bridge_name, 'up']):
                raise RuntimeError('ip link set %s up failed' % bridge_name)
        else:
            time.sleep(1)       # XXX: race

    def _init_bridge(self):
        for physical_network in self.interface_mappings:
            br_name = self._get_hub_name(physical_network)
            self._ensure_bridge(br_name)
            port_name = self._get_port_name(
                self.interface_mappings[physical_network])
            ip_wrapper = ip_lib.IPWrapper(self.root_helper)
            if not ip_lib.device_exists(port_name, self.root_helper):
                # ip link add $tunnel_interface type dummy
                device = ip_wrapper.add_dummy(port_name)
            else:
                device = ip_wrapper.device(port_name)
            device.link.set_up()
            self._execute(['brctl', 'addif', br_name, port_name])

    def _cleanup_bridge(self):
        for physical_network in self.interface_mappings:
            br_name = self._get_hub_name(physical_network)
            port_name = self._get_port_name(
                self.interface_mappings[physical_network])
            self._execute(['brctl', 'delif', br_name, port_name])

    def _make_vif_args(self, instance_id, network_id, vif_uuid, mac,
                       bridge_name):
        if not bridge_name:
            bridge_name = self._get_vif_bridge_name(network_id, vif_uuid)
        interface, vif = super(QuantumFakeVMAgentLB, self)._make_vif_args(
            instance_id, network_id, vif_uuid, mac, bridge_name)
        network, mapping = vif
        network['bridge_interface'] = None
        mapping['should_create_bridge'] = True
        return (interface, vif)

    def _probe_plug(self, network_id, vif_uuid, mac):
        br_veth_name, vm_veth_name = self._get_veth_pair_names(vif_uuid)
        # linuxbridge plugin agent adds the interface having a name which
        # starts with 'tap' to a bridge
        br_veth_name = self._get_tap_name(vif_uuid)
        ns_name = self._get_ns_name(vif_uuid)
        self._create_probe(br_veth_name, vm_veth_name, mac, None, ns_name)

    def _probe_unplug(self, network_id, vif_uuid):
        br_veth_name, vm_veth_name = self._get_veth_pair_names(vif_uuid)
        br_veth_name = self._get_tap_name(vif_uuid)
        br_name = self._get_vif_bridge_name(network_id, vif_uuid)
        ns_name = self._get_ns_name(vif_uuid)
        self._delete_probe(br_veth_name, vm_veth_name, br_name, ns_name)
