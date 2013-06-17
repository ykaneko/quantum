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

import ast
import sys

from oslo.config import cfg

# This is necessary for '_'. copied from nova/cmd/__init__.py.
from nova.openstack.common import gettextutils
gettextutils.install('fakevm-vif')

from nova.network import model

from quantum.openstack.common import importutils


class QuantumFakeVMVifWrapper(object):
    def __init__(self, conf):
        super(QuantumFakeVMVifWrapper, self).__init__()
        vif_class = importutils.import_class(conf.libvirt_vif_driver)
        self.vif_driver = vif_class(None)

    def run(self, cmd):
        if cmd.name == 'plug':
            return self._plug(cmd)
        elif cmd.name == 'unplug':
            return self._unplug(cmd)
        elif cmd.name == 'bridge-name':
            return self._bridge_name(cmd)
        sys.stderr.write(_('unknown command') + '\n')
        sys.exit(1)

    def _make_args(self, cmd):
        instance = {
            'host': cmd.vif_host,
            'uuid': cmd.instance_id,
        }
        network = {}
        if cmd.bridge_name:
            network['bridge'] = cmd.bridge_name
        mapping = {
            'vif_type': cmd.vif_type,
            'vif_uuid': cmd.vif_uuid,
            'mac': cmd.mac,
        }
        if cmd.vif_type == model.VIF_TYPE_OVS:
            mapping['ovs_interfaceid'] = cmd.vif_uuid
        vif = (network, mapping)
        return (instance, vif)

    def _plug(self, cmd):
        instance = ast.literal_eval(cmd.instance)
        vif = ast.literal_eval(cmd.vif)
        self.vif_driver.plug(instance, vif)

    def _unplug(self, cmd):
        instance = ast.literal_eval(cmd.instance)
        vif = ast.literal_eval(cmd.vif)
        self.vif_driver.unplug(instance, vif)

    def _bridge_name(self, cmd):
        sys.stdout.write('%s' % self.vif_driver.get_br_name(cmd.vif_uuid))


def add_cmd_parsers(subparsers):
    plug_act = subparsers.add_parser('plug')
    plug_act.add_argument('instance')
    plug_act.add_argument('vif')

    unplug_act = subparsers.add_parser('unplug')
    unplug_act.add_argument('instance')
    unplug_act.add_argument('vif')

    unplug_act = subparsers.add_parser('bridge-name')
    unplug_act.add_argument('vif_uuid')


def main():
    cfg.CONF.import_opt('libvirt_vif_driver', 'nova.virt.libvirt.driver')
    cfg.CONF.register_cli_opt(
        cfg.SubCommandOpt('cmd', handler=add_cmd_parsers))
    cfg.CONF(args=sys.argv[1:], project='nova')

    return QuantumFakeVMVifWrapper(cfg.CONF).run(cfg.CONF.cmd)


if __name__ == '__main__':
    main()
