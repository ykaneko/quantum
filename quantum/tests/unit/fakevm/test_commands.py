# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2013, Yoshihiro Kaneko
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

import mock

from quantum.fakevm import commands
from quantum.tests import base


class MyApp(object):
    def __init__(self, _stdout):
        self.stdout = _stdout


class TestFakeVMCommands(base.BaseTestCase):
    def setUp(self):
        super(TestFakeVMCommands, self).setUp()
        self.addCleanup(mock.patch.stopall)
        mock_std = mock.Mock()
        self.app = MyApp(mock_std)

        context_inst = mock.Mock()
        self.context = context_inst
        context_p = mock.patch(
            'quantum.context.get_admin_context_without_session',
            return_value=context_inst)
        context_p.start()

        rpc = mock.Mock()
        self.rpc = rpc
        self.app.fakevm_rpcapi = rpc

        client_inst = mock.Mock()
        fake_network = {'network': {'id': 'fake_net',
                                    'tenant_id': 'fake_tenant',
                                    'subnets': ['fake_subnet']}}
        fake_port = {'port':
                    {'id': 'fake_port',
                     'device_owner': 'qfakevm:vmport:i-xxxx',
                     'mac_address': 'aa:bb:cc:dd:ee:ffa',
                     'network_id': 'fake_net',
                     'tenant_id': 'fake_tenant',
                     'fixed_ips': [{'subnet_id': 'fake_subnet',
                                    'ip_address': '10.0.0.3'}]
                     }}

        client_inst.create_port = mock.Mock(return_value=fake_port)
        client_inst.show_network = mock.Mock(return_value=fake_network)
        client_inst.show_port = mock.Mock(return_value=fake_port)
        self.client = client_inst
        client_manager = mock.Mock()
        client_manager.quantum = client_inst
        self.app.client_manager = client_manager

    def _test_create_port(self, bridge=None):
        cmd = commands.CreatePort(self.app, None)
        cmd_parser = cmd.get_parser('create-port')
        args = ['--host', 'guest1', 'fake_net', 'i-xxxx']
        if bridge:
            args.append(bridge)
        parsed_args = cmd_parser.parse_args(args)
        cmd.run(parsed_args)

        fake_port = {'port':
                    {'admin_state_up': True,
                     'network_id': 'fake_net',
                     'device_id': 'i-xxxx',
                     'device_owner': 'qfakevm:vmport:i-xxxx',
                     'tenant_id': 'fake_tenant',
                     'fixed_ips': [{'subnet_id': 'fake_subnet'}]}}
        self.client.assert_has_calls([mock.call.create_port(fake_port)])
        self.rpc.assert_has_calls([mock.call.plug(self.context,
                                                  'guest1',
                                                  'i-xxxx',
                                                  'fake_net',
                                                  'fake_port',
                                                  'aa:bb:cc:dd:ee:ffa',
                                                  bridge)])

    def test_create_port(self):
        self._test_create_port()

    def test_create_port_with_bridge(self):
        self._test_create_port('br-fake')

    def _test_delete_port(self, bridge=None):
        cmd = commands.DeletePort(self.app, None)
        cmd_parser = cmd.get_parser('delete-probe')
        args = ['--host', 'guest1', 'fake_port']
        if bridge:
            args.append(bridge)
        parsed_args = cmd_parser.parse_args(args)
        cmd.run(parsed_args)

        self.client.assert_has_calls([mock.call.show_port('fake_port'),
                                      mock.call.delete_port('fake_port')])
        self.rpc.assert_has_calls([mock.call.unplug(self.context,
                                                    'guest1',
                                                    'fake_net',
                                                    'fake_port',
                                                    bridge)])

    def test_delete_port(self):
        self._test_delete_port()

    def test_delete_port_with_bridge(self):
        self._test_delete_port('br-fake')

    def _test_migrate(self, dst_host):
        cmd = commands.Migrate(self.app, None)
        cmd_parser = cmd.get_parser('migrate')
        args = ['--host', 'guest1',
                '--src-bridge-name', 'br-int1',
                '--dst-bridge-name', 'br-int2']
        if dst_host is not None:
            args += [dst_host]
        args += ['fake_port']
        parsed_args = cmd_parser.parse_args(args)
        cmd.run(parsed_args)

        self.client.assert_has_calls([mock.call.show_port('fake_port')])
        self.rpc.assert_has_calls([mock.call.plug(self.context,
                                                  dst_host,
                                                  'i-xxxx',
                                                  'fake_net',
                                                  'fake_port',
                                                  'aa:bb:cc:dd:ee:ffa',
                                                  'br-int2'),
                                   mock.call.unplug(self.context,
                                                    'guest1',
                                                    'fake_net',
                                                    'fake_port',
                                                    'br-int1')])

    def test_migrate(self):
        self._test_migrate('guest2')

    def test_migrate_without_dst_host(self):
        self.assertRaises(ValueError, self._test_migrate, (''))

    def test_migrate_src_eq_dst(self):
        self.assertRaises(ValueError, self._test_migrate, ('guest1'))

    def _test_plug(self, brname=None):
        cmd = commands.Plug(self.app, None)
        cmd_parser = cmd.get_parser('plug')
        args = ['--host', 'guest1', 'fake_port']
        if brname:
            args.append(brname)
        parsed_args = cmd_parser.parse_args(args)
        cmd.run(parsed_args)

        self.client.assert_has_calls([mock.call.show_port('fake_port')])
        self.rpc.assert_has_calls([mock.call.plug(self.context,
                                                  'guest1',
                                                  'i-xxxx',
                                                  'fake_net',
                                                  'fake_port',
                                                  'aa:bb:cc:dd:ee:ffa',
                                                  brname)])

    def test_plug(self):
        self._test_plug()

    def test_plug_with_bridge(self):
        self._test_plug('br-fake')

    def _test_unplug(self, brname=None):
        cmd = commands.Unplug(self.app, None)
        cmd_parser = cmd.get_parser('unplug')
        args = ['--host', 'guest1', 'fake_port']
        if brname:
            args.append(brname)
        parsed_args = cmd_parser.parse_args(args)
        cmd.run(parsed_args)

        self.client.assert_has_calls([mock.call.show_port('fake_port')])
        self.rpc.assert_has_calls([mock.call.unplug(self.context,
                                                    'guest1',
                                                    'fake_net',
                                                    'fake_port',
                                                    brname)])

    def test_unplug(self):
        self._test_unplug()

    def test_unplug_with_bridge(self):
        self._test_unplug('br-fake')

    def _test_unplug_all_host(self, brname=None):
        cmd = commands.UnplugAllHost(self.app, None)
        cmd_parser = cmd.get_parser('unplug-all-host')
        args = ['fake_port']
        if brname:
            args.append(brname)
        parsed_args = cmd_parser.parse_args(args)
        cmd.run(parsed_args)

        self.client.assert_has_calls([mock.call.show_port('fake_port')])
        self.rpc.assert_has_calls([mock.call.unplug_all_host(self.context,
                                                             'fake_net',
                                                             'fake_port',
                                                             brname)])

    def test_unplug_all_host(self):
        self._test_unplug_all_host()

    def test_unplug_all_host_with_bridge(self):
        self._test_unplug_all_host('br-fake')

    def test_exec_command(self):
        cmd = commands.ExecCommand(self.app, None)
        cmd_parser = cmd.get_parser('exec')
        args = ['--host', 'guest1', 'fake_port', 'abcd']
        parsed_args = cmd_parser.parse_args(args)
        cmd.run(parsed_args)

        self.rpc.assert_has_calls([mock.call.exec_command(self.context,
                                                          'guest1',
                                                          'fake_port',
                                                          'abcd')])
