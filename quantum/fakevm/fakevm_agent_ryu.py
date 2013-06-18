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

import os.path

from oslo.config import cfg

from quantum.agent.linux import ip_lib
from quantum.extensions import portbindings
from quantum.fakevm import fakevm_agent_plugin_base
from quantum.plugins.ryu.common import config


class QuantumFakeVMAgentRyu(
        fakevm_agent_plugin_base.QuantumFakeVMAgentPluginBase):
    OPTS = [
        cfg.StrOpt('vir_bridge', default='br-fakevm',
                   help='bridge name to emulate multiple node'),
        cfg.BoolOpt('use_tunnel', default=True,
                   help='use tunnel or not (set True when gre tunneling app)'),
    ]

    def __init__(self):
        super(QuantumFakeVMAgentRyu, self).__init__()
        self.conf = None
        self.root_helper = None
        self.int_br = None

    def init(self, conf):
        super(QuantumFakeVMAgentRyu, self).init(conf)
        self.vif_type = portbindings.VIF_TYPE_OVS
        if self.conf.FAKEVM.allow_multi_node_emulate:
            if self.conf.FAKEVM.use_tunnel:
                self._init_tunnel()
            else:
                self._init_bridge()

    def cleanup(self):
        if self.conf.FAKEVM.use_tunnel:
            self._cleanup_tunnel()
        else:
            self._cleanup_bridge()

    def _get_vif_bridge_name(self, network_id, vif_uuid):
        return self.conf.OVS.integration_bridge

    def _init_bridge(self):
        br_name = self.conf.FAKEVM.vir_bridge
        self._ensure_bridge(br_name)
        self.int_br = self._ensure_ovs_bridge(self.conf.OVS.integration_bridge)
        ovs_veth_name = ('qfo' + self.conf.FAKEVM.host)[:self.DEV_NAME_LEN]
        br_veth_name = ('qfb' + self.conf.FAKEVM.host)[:self.DEV_NAME_LEN]
        self._connect_ovs_lb(ovs_veth_name, br_veth_name, self.int_br, br_name)

    def _cleanup_bridge(self):
        br_name = self.conf.FAKEVM.vir_bridge
        ovs_veth_name = ('qfo' + self.conf.FAKEVM.host)[:self.DEV_NAME_LEN]
        br_veth_name = ('qfb' + self.conf.FAKEVM.host)[:self.DEV_NAME_LEN]
        self._disconnect_ovs_lb(ovs_veth_name, br_veth_name, self.int_br,
                                br_name)

    def _init_tunnel(self):
        self._cleanup_tunnel()
        dev_name = self.conf.OVS.tunnel_interface
        ip_wrapper = ip_lib.IPWrapper(self.root_helper)
        device = None
        if not ip_lib.device_exists(dev_name, self.root_helper):
            # ip link add $tunnel_interface type dummy
            device = ip_wrapper.add_dummy(dev_name)
        elif os.path.exists('/sys/devices/virtual/net/%s' % dev_name):
            device = ip_wrapper.device(dev_name)
        if device and self.conf.OVS.tunnel_ip:
            # ip address add $tunnel_ip brd '+' scope global dev $dev_name
            device.addr.add(4, self.conf.OVS.tunnel_ip, '+')
            # ip link set $tunnel_interface up
            device.link.set_up()
            self._execute(['ip', 'route', 'add', self.conf.OVS.tunnel_ip,
                           'dev', dev_name])

        self._ensure_ovs_bridge(self.conf.OVS.integration_bridge)

    def _cleanup_tunnel(self):
        dev_name = self.conf.OVS.tunnel_interface
        if dev_name and self.conf.OVS.tunnel_ip:
            if (ip_lib.device_exists(dev_name, self.root_helper) and
                os.path.exists('/sys/devices/virtual/net/%s' % dev_name)):
                ip_wrapper = ip_lib.IPWrapper(self.root_helper)
                ip_wrapper.device(dev_name).link.delete()


cfg.CONF.register_opts(QuantumFakeVMAgentRyu.OPTS, 'FAKEVM')
