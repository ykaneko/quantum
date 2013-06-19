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

import re

from oslo.config import cfg

from quantum.agent.linux import ip_lib
from quantum.agent.linux import ovs_lib
from quantum.common import config

AGENT_OPTS = [
    cfg.StrOpt('root_helper', default='sudo'),
]


_BRIDGE_PATTERN = '^qbr.*'
_OVS_PORT_PATTERN = '^qvo.*'                    # this will also delete qvb
_FAKEVM_INTERFACE_PATTERN = '^(qft|qfb|qvb).+'  # this will also delete qfv
_FAKEVM_NS_PATTERN = '^fakevm-.+-.+'


def is_vif_bridge(dev_name):
    return re.match(_BRIDGE_PATTERN, dev_name)


def is_ovs_port(dev_name):
    return re.match(_OVS_PORT_PATTERN, dev_name)


def is_fakevm_interface(dev_name):
    return re.match(_FAKEVM_INTERFACE_PATTERN, dev_name)


def is_fakevm_ns(ns_name):
    return re.match(_FAKEVM_NS_PATTERN, ns_name)


def main():
    conf = cfg.CONF
    conf.register_opts(AGENT_OPTS, 'AGENT')
    config.setup_logging(conf)
    conf()

    ip_wrapper = ip_lib.IPWrapper(conf.AGENT.root_helper)

    print 'bridges:'
    bridges = [dev for dev in ip_wrapper.get_devices()
               if is_vif_bridge(dev.name)]
    for br in bridges:
        print br.name
        br.link.delete()

    print 'ovs ports:'
    ovs_ports = [dev for dev in ip_wrapper.get_devices()
                 if is_ovs_port(dev.name)]
    for port in ovs_ports:
        print port.name
        bridge_name = ovs_lib.get_bridge_for_iface(conf.AGENT.root_helper,
                                                   port.name)
        if bridge_name:
            bridge = ovs_lib.OVSBridge(bridge_name, conf.AGENT.root_helper)
            bridge.delete_port(port.name)
        port.link.delete()

    print 'fakevm interfaces:'
    devices = [dev for dev in ip_wrapper.get_devices()
               if is_fakevm_interface(dev.name)]
    for dev in devices:
        print dev.name
        dev.link.delete()

    print 'fakevm namespace:'
    ns_names = [ns for ns in
                ip_lib.IPWrapper.get_namespaces(conf.AGENT.root_helper)
                if is_fakevm_ns(ns)]
    for ns_name in ns_names:
        # TODO kill dhcp client
        print ns_name
        ip_wrapper.netns.delete(ns_name)
