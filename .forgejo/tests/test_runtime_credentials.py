import ast
import base64
import os
import copy
import importlib.util
import json
import pathlib
import unittest

spec = importlib.util.spec_from_file_location('bootstrap', pathlib.Path(__file__).parents[1] / 'scripts/ensure-runtime-credentials.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Cluster:
    def __init__(self):
        self.secret = {'metadata': {'resourceVersion': '1'}}
        self.sources = [{'metadata': {'uid': n, 'resourceVersion': '1'}, 'spec': {
            'replicas': 1, 'selector': {'matchLabels': {'app': n}}, 'template': {'spec': {
                'containers': [{'name': n, 'image': 'old', 'env': []}]}}}} for n in m.DEPLOYMENTS]
        self.values = {k: 'fixture-' + k for k in m.KEYS.values()}
        self.patches = 0
        self.reads = 0
        self.disagree = False
        self.fail = False
        self.drift = False

    def call(self, *args, data=None):
        if self.fail:
            raise m.BootstrapError('unavailable')
        if args[:2] == ('get', 'secret'):
            return copy.deepcopy(self.secret)
        if args[:2] == ('get', 'deployment'):
            source = copy.deepcopy(self.sources[m.DEPLOYMENTS.index(args[2])])
            if self.drift and self.reads:
                source['metadata']['resourceVersion'] = '2'
            return source
        if args[:2] == ('get', 'pods'):
            name = args[3].split('=')[1]
            return {'items': [{'metadata': {'name': name + '-pod', 'uid': name},
                               'spec': {'containers': [{'name': name, 'image': 'old'}]},
                               'status': {'phase': 'Running', 'containerStatuses': [{
                                   'ready': True, 'containerID': name, 'restartCount': 0}]}}]}
        if args[0] == 'exec':
            self.reads += 1
            values = dict(self.values)
            if self.disagree and self.reads == 2:
                values['django-secret'] = 'different-fixture'
            return values
        if args[0] == 'patch':
            patch = json.loads(data)
            assert patch[0] == {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '1'}
            self.patches += 1
            self.secret.update(data=patch[1]['value'], immutable=patch[2]['value'])
            return copy.deepcopy(self.secret)
        raise AssertionError(args)


class BootstrapTests(unittest.TestCase):
    def test_complete_projection_compares_all_roles_twice(self):
        c = Cluster()
        m.ensure(c.call)
        self.assertEqual(c.reads, 2)
        self.assertEqual(c.patches, 1)
        self.assertTrue(c.secret['immutable'])
        self.assertEqual(c.secret['data'], {k: base64.b64encode(v.encode()).decode() for k, v in c.values.items()})
        m.ensure(c.call)
        self.assertEqual(c.patches, 1)

    def test_disagreement_refuses_write(self):
        c = Cluster(); c.disagree = True
        with self.assertRaises(m.BootstrapError): m.ensure(c.call)
        self.assertEqual(c.patches, 0)

    def test_changed_source_refuses_write(self):
        c = Cluster(); c.drift = True
        with self.assertRaises(m.BootstrapError): m.ensure(c.call)
        self.assertEqual(c.patches, 0)

    def test_failure_refuses_write(self):
        c = Cluster(); c.fail = True
        with self.assertRaises(m.BootstrapError): m.ensure(c.call)
        self.assertEqual(c.patches, 0)

    def test_conflicting_target_never_overwritten(self):
        c = Cluster(); c.secret.update(data={'django-secret': 'other'}, immutable=True)
        with self.assertRaises(m.BootstrapError): m.ensure(c.call)
        self.assertEqual(c.patches, 0)

    def test_missing_effective_value_refuses_write(self):
        c = Cluster(); c.values['django-secret'] = ''
        with self.assertRaises(m.BootstrapError): m.ensure(c.call)
        self.assertEqual(c.patches, 0)

    def test_configured_replay_needs_no_exec(self):
        c = Cluster(); m.ensure(c.call)
        for source in c.sources:
            source['spec']['template']['spec']['containers'][0]['env'] = [
                {'name': env, 'valueFrom': {'secretKeyRef': {'name': m.SECRET, 'key': key}}}
                for env, key in m.KEYS.items()]
        before = c.reads
        m.ensure(c.call)
        self.assertEqual(c.reads, before)
        self.assertEqual(c.patches, 1)

    def test_unstable_source_pod_refuses_write(self):
        c = Cluster(); c.sources[0]['spec']['replicas'] = 2
        with self.assertRaises(m.BootstrapError): m.ensure(c.call)
        self.assertEqual(c.patches, 0)


class SettingsConfigurationTests(unittest.TestCase):
    def settings_credentials(self, environment):
        source = pathlib.Path(__file__).parents[2] / 'drivemanager/settings.py'
        tree = ast.parse(source.read_text())
        # Exercise the source-owned import-free credential boundary, including
        # its validation dependency; adjacent assignment slicing drops helpers.
        nodes = [n for n in tree.body if
                 (isinstance(n, ast.FunctionDef) and n.name == '_required_credential') or
                 (isinstance(n, ast.Assign) and any(
                     isinstance(t, ast.Name) and t.id in {'SECRET_KEY', 'JWT_SECRET_KEY'}
                     for t in n.targets))]
        self.assertEqual(len(nodes), 3)
        code = compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec')
        from unittest.mock import patch
        with patch.dict(os.environ, environment, clear=True):
            scope = {'os': os}
            exec(code, scope)
            return scope

    def test_actual_settings_reject_missing_or_blank_secret_and_preserve_bytes(self):
        for environment in ({}, {'SECRET_KEY': ''}, {'SECRET_KEY': '   '}):
            with self.subTest(environment=environment), self.assertRaises(RuntimeError):
                self.settings_credentials(environment)
        scope = self.settings_credentials({'SECRET_KEY': ' synthetic fixture '})
        self.assertEqual(scope['SECRET_KEY'], ' synthetic fixture ')
        self.assertEqual(scope['JWT_SECRET_KEY'], ' synthetic fixture ')

    def test_explicit_jwt_alias_preserves_bytes_and_blank_refuses_fallback(self):
        for value in ('', '   '):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.settings_credentials({'SECRET_KEY': 'configured fixture',
                                           'JWT_SECRET_KEY': value})
        scope = self.settings_credentials({'SECRET_KEY': 'configured fixture',
                                          'JWT_SECRET_KEY': ' separate jwt fixture '})
        self.assertEqual(scope['JWT_SECRET_KEY'], ' separate jwt fixture ')


if __name__ == '__main__': unittest.main()
