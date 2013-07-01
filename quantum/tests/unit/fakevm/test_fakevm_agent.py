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

import logging as std_logging

import mock

from quantum.debug.fakevm import fakevm_agent
from quantum.tests import base


_AGENT_NAME = 'quantum.debug.fakevm.fakevm_agent'


class TestFakeVMAgent(base.BaseTestCase):
    def setUp(self):
        super(TestFakeVMAgent, self).setUp()
        self.addCleanup(mock.patch.stopall)

        self.mock_cfg = mock.patch(_AGENT_NAME + '.cfg').start()
        self.mock_conf = mock.Mock()
        self.mock_cfg.CONF = self.mock_conf

        self.agent_config = mock.patch(_AGENT_NAME + '.config').start()
        self.fakevm_rpc = mock.patch(_AGENT_NAME + '.fakevm_rpc').start()
        self.rpc_api = mock.Mock()

        self.rpc_api.get_topic_name = mock.Mock(return_value='topic')
        self.fakevm_rpc.FakeVMRpcApi = self.rpc_api

        self.agent_plugin = mock.Mock()
        self.import_object = mock.Mock(return_value=self.agent_plugin)
        self.importutils = mock.patch(_AGENT_NAME + '.importutils').start()
        self.importutils.import_object = self.import_object

        self.logging = mock.patch(_AGENT_NAME + '.logging').start()
        self.log = mock.patch(_AGENT_NAME + '.LOG').start()

        self.quantum_rpc = mock.patch(_AGENT_NAME + '.rpc').start()
        self.dispatcher = mock.patch(_AGENT_NAME + '.dispatcher').start()
        self.rpcdispatcher = mock.Mock()
        self.dispatcher.RpcDispatcher = mock.Mock(
            return_value=self.rpcdispatcher)

        self.connection = mock.Mock()
        self.quantum_rpc.create_connection = mock.Mock(
            return_value=self.connection)

        self.conf = mock.Mock()
        self.conf.FAKEVM.host = 'host1'
        self.conf.FAKEVM.fakevm_agent_plugin = 'fakevm_agent_plugin'

    def mock_agent(self):
        return fakevm_agent.QuantumFakeVMAgent(self.conf)

    def test_instantiate(self):
        agent = self.mock_agent()

        self.import_object.assert_has_calls([
            mock.call('fakevm_agent_plugin')
        ])
        self.agent_plugin.assert_has_calls([
            mock.call.init(self.conf)
        ])
        self.rpc_api.assert_has_calls([
            mock.call.get_topic_name('fakevm_agent', 'host1'),
            mock.call.get_topic_name('fakevm_agent')
        ])
        self.quantum_rpc.assert_has_calls([
            mock.call.create_connection(new=True)
        ])
        self.dispatcher.assert_has_calls([
            mock.call.RpcDispatcher([agent])
        ])
        self.connection.assert_has_calls([
            mock.call.create_consumer('topic', self.rpcdispatcher,
                                      fanout=False),
            mock.call.create_consumer('topic', self.rpcdispatcher,
                                      fanout=True),
            mock.call.consume_in_thread()
        ])
        self.conf.assert_has_calls([
            mock.call.log_opt_values(self.log, std_logging.DEBUG)
        ])

    def _test_plug(self, bridge_name):
        agent = self.mock_agent()

        expected = ['i-xxxx', 'fake_net', 'fake_port', 'aa:bb:cc:dd:ee:ff',
                    bridge_name]

        agent.plug('context', *expected)

        self.agent_plugin.assert_has_calls([
            mock.call.init(self.conf),
            mock.call.plug(*expected)
        ])

    def test_plug(self):
        self._test_plug(None)

    def test_plug_with_bridge(self):
        self._test_plug('br-int')

    def _test_unplug(self, bridge_name):
        agent = self.mock_agent()

        expected = ['fake_net', 'fake_port', bridge_name]

        agent.unplug('context', *expected)

        self.agent_plugin.assert_has_calls([
            mock.call.init(self.conf),
            mock.call.unplug(*expected)
        ])

    def test_unplug(self):
        self._test_unplug(None)

    def test_unplug_with_bridge(self):
        self._test_unplug('br-int')

    def _test_unplug_all_host(self, bridge_name):
        agent = self.mock_agent()

        expected = ['fake_net', 'fake_port', bridge_name]

        agent.unplug_all_host('context', *expected)

        self.agent_plugin.assert_has_calls([
            mock.call.init(self.conf),
            mock.call.unplug(*expected)
        ])

    def test_unplug_all_host(self):
        self._test_unplug_all_host(None)

    def test_unplug_all_host_with_bridge(self):
        self._test_unplug_all_host('br-int')

    def test_exec_command(self):
        agent = self.mock_agent()

        expected = ['fake_port', 'command']

        agent.exec_command('context', *expected)

        self.agent_plugin.assert_has_calls([
            mock.call.init(self.conf),
            mock.call.exec_command(*expected)
        ])

    def test_main(self):
        agent = mock.Mock()

        with mock.patch(_AGENT_NAME + '.QuantumFakeVMAgent',
                        return_value=agent) as mock_agent:
            fakevm_agent.main()

        self.mock_conf.assert_has_calls([
            mock.call.register_cli_opts(mock_agent.OPTS, 'FAKEVM'),
        ])
        self.agent_config.assert_has_calls([
            mock.call.register_agent_state_opts_helper(self.mock_conf),
            mock.call.register_root_helper(self.mock_conf),
            mock.call.setup_logging(self.mock_conf)
        ])
        self.mock_cfg.assert_has_calls([
            mock.call.CONF(project='quantum')
        ])
        mock_agent.assert_has_calls([
            mock.call(self.mock_conf)
        ])
        agent.assert_has_calls([
            mock.call.wait_rpc()
        ])
