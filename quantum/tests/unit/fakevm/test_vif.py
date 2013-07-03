# Copyright (c) 2013 OpenStack Foundation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from contextlib import nested
import sys

import mock

from quantum.openstack.common import importutils
from quantum.tests import base


def patch_fake_nova():
    nova_mod = mock.Mock()
    nova_openstack_mod = nova_mod.openstack
    nova_openstack_common = nova_openstack_mod.common
    nova_gettextutils = nova_openstack_common.gettextutils
    return mock.patch.dict(
        'sys.modules',
        {'nova': nova_mod,
         'nova.openstack': nova_openstack_mod,
         'nova.openstack.common': nova_openstack_common,
         'nova.openstack.common.gettextutils': nova_gettextutils})


class TestFakeVMVif(base.BaseTestCase):
    def setUp(self):
        super(TestFakeVMVif, self).setUp()
        self.addCleanup(mock.patch.stopall)
        self.nova = patch_fake_nova().start()
        self.mod_vif = importutils.import_module('quantum.debug.fakevm.vif')

        self.mock_cfg = mock.patch('quantum.debug.fakevm.vif.cfg').start()
        self.mock_conf = mock.Mock()
        self.mock_cfg.CONF = self.mock_conf
        self.mock_subcommandopt = mock.Mock()
        self.mock_cfg.SubCommandOpt = mock.Mock(
            return_value=self.mock_subcommandopt)
        self.vif_driver = mock.Mock()
        self.vif_class = mock.Mock(return_value=self.vif_driver)
        self.import_class = mock.Mock(return_value=self.vif_class)
        self.importutils = mock.patch(
            'quantum.debug.fakevm.vif.importutils').start()
        self.importutils.import_class = self.import_class

    def mock_vif(self):
        self.conf = mock.Mock()
        self.conf.libvirt_vif_driver = 'vifdriver'
        return self.mod_vif.QuantumFakeVMVifWrapper(self.conf)

    def test_init(self):
        vif = self.mock_vif()

        self.assertEqual(vif.vif_driver, self.vif_driver)

    def test_run_plug(self):
        vif = self.mock_vif()

        cmd = mock.Mock()
        cmd.name = 'plug'
        with nested(
            mock.patch.object(vif, '_plug'),
            mock.patch.object(vif, '_unplug'),
            mock.patch.object(vif, '_bridge_name')
        ) as (mock_plug, mock_unplug, mock_bridge_name):
            vif.run(cmd)

        mock_plug.assert_has_calls([
            mock.call(cmd)
        ])
        self.assertEqual(mock_unplug.call_count, 0)
        self.assertEqual(mock_bridge_name.call_count, 0)

    def test_run_unplug(self):
        vif = self.mock_vif()

        cmd = mock.Mock()
        cmd.name = 'unplug'
        with nested(
            mock.patch.object(vif, '_plug'),
            mock.patch.object(vif, '_unplug'),
            mock.patch.object(vif, '_bridge_name')
        ) as (mock_plug, mock_unplug, mock_bridge_name):
            vif.run(cmd)

        self.assertEqual(mock_plug.call_count, 0)
        mock_unplug.assert_has_calls([
            mock.call(cmd)
        ])
        self.assertEqual(mock_bridge_name.call_count, 0)

    def test_run_bridge_name(self):
        vif = self.mock_vif()

        cmd = mock.Mock()
        cmd.name = 'bridge-name'
        with nested(
            mock.patch.object(vif, '_plug'),
            mock.patch.object(vif, '_unplug'),
            mock.patch.object(vif, '_bridge_name')
        ) as (mock_plug, mock_unplug, mock_bridge_name):
            vif.run(cmd)

        self.assertEqual(mock_plug.call_count, 0)
        self.assertEqual(mock_unplug.call_count, 0)
        mock_bridge_name.assert_has_calls([
            mock.call(cmd)
        ])

    def test_run_unknown(self):
        vif = self.mock_vif()

        cmd = mock.Mock()
        cmd.name = 'unknown'
        with nested(
            mock.patch.object(vif, '_plug'),
            mock.patch.object(vif, '_unplug'),
            mock.patch.object(vif, '_bridge_name')
        ) as (mock_plug, mock_unplug, mock_bridge_name):
            self.assertRaises(SystemExit, vif.run, (cmd))

        self.assertEqual(mock_plug.call_count, 0)
        self.assertEqual(mock_unplug.call_count, 0)
        self.assertEqual(mock_bridge_name.call_count, 0)

    def test_plug(self):
        vif = self.mock_vif()

        instance = {'host': 'host1', 'uuid': 'i-xxxxx'}
        network = {'id': 'fake_net', 'bridge': 'brname'}
        mapping = {'vif_type': 'bridge', 'vif_uuid': 'fake_port',
                   'mac': 'aa:bb:cc:dd:ee:ff'}
        cmd = mock.Mock()
        cmd.instance = str(instance)
        cmd.vif = str([network, mapping])

        vif._plug(cmd)

        self.vif_driver.assert_has_calls([
            mock.call.plug(instance, [network, mapping])
        ])

    def test_unplug(self):
        vif = self.mock_vif()

        instance = {'host': 'host1', 'uuid': 'i-xxxxx'}
        network = {'id': 'fake_net', 'bridge': 'brname'}
        mapping = {'vif_type': 'bridge', 'vif_uuid': 'fake_port',
                   'mac': 'aa:bb:cc:dd:ee:ff'}
        cmd = mock.Mock()
        cmd.instance = str(instance)
        cmd.vif = str([network, mapping])

        vif._unplug(cmd)

        self.vif_driver.assert_has_calls([
            mock.call.unplug(instance, [network, mapping])
        ])

    def test_bridge_name(self):
        vif = self.mock_vif()

        cmd = mock.Mock()
        cmd.vif_uuid = 'fake_port'

        with nested(
            mock.patch('sys.stdout.write'),
            mock.patch.object(self.vif_driver, 'get_br_name',
                              return_value='brname')
        ) as (mock_write, mock_get_br_name):
            vif._bridge_name(cmd)

        mock_get_br_name.assert_has_calls([
            mock.call('fake_port')
        ])
        mock_write.assert_has_calls([
            mock.call('brname')
        ])

    def test_add_cmd_parsers(self):
        subparsers = mock.Mock()
        plug_act = mock.Mock()
        unplug_act = mock.Mock()
        brname_act = mock.Mock()
        unknown_act = mock.Mock()

        def add_parser(*args, **kwargs):
            if args[0] == 'plug':
                return plug_act
            elif args[0] == 'unplug':
                return unplug_act
            elif args[0] == 'bridge-name':
                return brname_act
            else:
                return unknown_act
        subparsers.add_parser = mock.Mock(side_effect=add_parser)

        self.mod_vif.add_cmd_parsers(subparsers)

        subparsers.assert_has_calls([
            mock.call.add_parser('plug'),
            mock.call.add_parser('unplug'),
            mock.call.add_parser('bridge-name')
        ])
        plug_act.assert_has_calls([
            mock.call.add_argument('instance'),
            mock.call.add_argument('vif'),
        ])
        unplug_act.assert_has_calls([
            mock.call.add_argument('instance'),
            mock.call.add_argument('vif'),
        ])
        brname_act.assert_has_calls([
            mock.call.add_argument('vif_uuid'),
        ])
        self.assertEqual(unknown_act.call_count, 0)

    def test_main(self):
        self.mock_conf.libvirt_vif_driver = 'vifdriver'
        self.mock_conf.cmd = 'command'
        with nested(
            mock.patch.object(self.mod_vif, 'QuantumFakeVMVifWrapper'),
            mock.patch.object(sys, 'argv', ['command', 'arg1', 'arg2'])
        ) as (mock_vif, mock_sys):
            self.mod_vif.main()

        self.mock_conf.assert_has_calls([
            mock.call.import_opt('libvirt_vif_driver',
                                 'nova.virt.libvirt.driver'),
            mock.call.register_cli_opt(self.mock_subcommandopt),
            mock.call(args=['arg1', 'arg2'], project='nova')
        ])
        self.mock_cfg.assert_has_calls([
            mock.call.SubCommandOpt('cmd',
                                    handler=self.mod_vif.add_cmd_parsers)
        ])
        mock_vif.assert_has_calls([
            mock.call(self.mock_conf)
        ])
