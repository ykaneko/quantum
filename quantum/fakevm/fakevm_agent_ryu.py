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

import time

from oslo.config import cfg

from quantum.agent.linux import ip_lib
from quantum.agent.linux import ovs_lib
from quantum.agent.linux import utils
from quantum.extensions import portbindings
from quantum.fakevm import fakevm_agent
from quantum.fakevm import fakevm_agent_plugin_base
from quantum.plugins.ryu.common import config


class QuantumFakeVMAgentRyu(
        fakevm_agent_plugin_base.QuantumFakeVMAgentPluginBase):
    _BRIDGE_PREFIX = 'qfbr-'

    OPTS = [
        cfg.StrOpt('vir_bridge', default=_BRIDGE_PREFIX + 'default',
                   help='bridge name to emulate multiple node'),
        cfg.StrOpt('use_tunnel', default=True,
                   help='use tunnel or not (set True when gre tunneling app)'),
    ]

    def __init__(self):
        super(QuantumFakeVMAgentRyu, self).__init__()
        self.conf = None
        self.root_helper = None
        self.int_br = None

    def init(self, quantum_conf):
        self.conf = quantum_conf
        self.root_helper = self.conf.AGENT.root_helper

        if self.conf.FAKEVM.use_tunnel:
            self._init_tunnel()
        else:
            self._init_bridge()

    def get_vif_type(self):
        return portbindings.VIF_TYPE_OVS

    def cleanup(self):
        if self.conf.FAKEVM.use_tunnel:
            self._cleanup_tunnel()
        else:
            self._cleanup_bridge()

    def _execute(self, command):
        return utils.execute(command, root_helper=self.root_helper)

    def _device_exists(self, device):
        try:
            self._execute(['ip', 'link', 'show', 'dev', device])
        except RuntimeError:
            return False
        return True

    def _ensure_ovs_br(self, ovs_bridge_name):
        ovs_br = ovs_lib.OVSBridge(ovs_bridge_name, self.root_helper)
        ovs_br.run_vsctl(['--', '--may-exist', 'add-br', ovs_bridge_name])
        return ovs_br

    def _bridge_exists(self, bridge_name):
        try:
            self._execute(['brctl', 'show', bridge_name])
        except RuntimeError:
            return False
        return True

    def _ensure_bridge(self, bridge_name):
        br_name = self.conf.FAKEVM.vir_bridge
        if not self._bridge_exists(br_name):
            if self._execute(['brctl', 'addbr', br_name]):
                raise RuntimeError('brctl addbr %s failed' % br_name)
            if self._execute(['brctl', 'setfd', br_name, str(0)]):
                raise RuntimeError('brctl setfd %s 0 failed' % br_name)
            if self._execute(['brctl', 'stp', br_name, 'off']):
                raise RuntimeError('brctl stp %s off failed' % br_name)
            if self._execute(['ip', 'link', 'set', br_name, 'up']):
                raise RuntimeError('ip link set %s up failed' % br_name)
        else:
            time.sleep(1)       # XXX: race

    def _get_port_name(self):
        return (self._BRIDGE_PREFIX +
                self.conf.FAKEVM.host)[:fakevm_agent.DEV_NAME_LEN]

    def _init_bridge(self):
        br_name = self.conf.FAKEVM.vir_bridge
        self._ensure_bridge(br_name)
        self.int_br = self._ensure_ovs_br(self.conf.OVS.integration_bridge)
        port_name = self._get_port_name()
        self.int_br.add_port(port_name)
        self._execute(['brctl', 'addif', br_name, port_name])

    def _cleanup_bridge(self):
        port_name = self._get_port_name()
        self._execute(['brctl', 'delif',
                       self.conf.FAKEVM.vir_bridge, port_name])
        self.int_br.delete_port(port_name)

    def _init_tunnel(self):
        self._cleanup_tunnel()
        dev_name = self.conf.OVS.tunnel_interface
        ip_wrapper = ip_lib.IPWrapper(self.root_helper)
        if not ip_lib.device_exists(dev_name, self.root_helper):
            # ip link add $tunnel_interface type dummy
            device = ip_wrapper.add_dummy(dev_name)
        else:
            device = ip_wrapper.device(dev_name)
        if self.conf.OVS.tunnel_ip:
            # ip address add $tunnel_ip brd '+' scope global dev $dev_name
            device.addr.add(4, self.conf.OVS.tunnel_ip, '+')
            # ip link set $tunnel_interface up
            device.link.set_up()

        self._ensure_ovs_br(self.conf.OVS.integration_bridge)

    def _cleanup_tunnel(self):
        dev_name = self.conf.OVS.tunnel_interface
        if dev_name and self.conf.OVS.tunnel_ip:
            if ip_lib.device_exists(dev_name, self.root_helper):
                ip_wrapper = ip_lib.IPWrapper(self.root_helper)
                ip_wrapper.device(dev_name).link.delete()


cfg.CONF.register_opts(QuantumFakeVMAgentRyu.OPTS, 'FAKEVM')
