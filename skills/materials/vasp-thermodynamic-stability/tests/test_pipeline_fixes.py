"""Offline regressions. Scientific tests skip explicitly if pymatgen cannot load."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
import warnings
import zipfile

SKILLS = Path(__file__).resolve().parents[2]
ROOT = SKILLS.parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    analyze = load('analyze', SKILLS / 'vasp-thermodynamic-stability/scripts/analyze_stability.py')
    setup = load('setup', SKILLS / 'vasp-thermodynamic-stability/scripts/setup_competing_phases.py')
    SCIENCE_ERROR = ''
except ImportError as exc:
    analyze = setup = None
    SCIENCE_ERROR = f'{type(exc).__name__}: {exc}'
results = load('results', SKILLS / 'vasp-thermodynamic-stability/scripts/hpc_results.py')
evidence = load('evidence', SKILLS / 'experiment-report/scripts/collect_evidence.py')
engine = load('engine', ROOT / 'material_MCP/mcp_servers/mattergen_generate_server/mattergen_engine.py')


class FixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.info = self.path / 'submission_info.json'
        self.info.write_text(json.dumps([
            {'dirname': 'Mg_2at', 'formula': 'Mg', 'natoms': 2},
            {'dirname': 'O2_8at', 'formula': 'O2', 'natoms': 8},
            {'dirname': 'MgO_8at', 'formula': 'MgO', 'natoms': 8, 'is_target': True},
        ]), encoding='utf-8')
        self.energies = {'Mg_2at': -2.0, 'O2_8at': -10.0, 'MgO_8at': -14.0}

    def tearDown(self):
        self.temp.cleanup()

    @unittest.skipIf(analyze is None, SCIENCE_ERROR)
    def test_cell_normalization_and_hull(self):
        records = {name: {'energy_eV': energy * factor, 'basis': 'cell', 'natoms': natoms}
                   for (name, energy), factor, natoms in zip(self.energies.items(), (2, 4, 4), (2, 8, 8))}
        entries = analyze.build_entries_from_local(records, self.info)
        self.assertEqual([e.energy for e in entries], [-2, -10, -14])
        answer = analyze.analyze_stability(entries, 'MgO')
        self.assertEqual(answer['ehull'], 0)
        self.assertEqual(answer['formation_energy'], -3.5)

    @unittest.skipIf(analyze is None, SCIENCE_ERROR)
    def test_legacy_warns(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            entries = analyze.build_entries_from_local(self.energies, self.info)
        self.assertTrue(caught)
        self.assertEqual([e.energy for e in entries], list(self.energies.values()))

    @unittest.skipIf(analyze is None, SCIENCE_ERROR)
    def test_invalid_energy_rejected(self):
        with self.assertRaises(ValueError):
            analyze.build_entries_from_local({'Mg_2at': -2}, self.info)
        for bad in (float('nan'), float('inf'), True, None, {'energy_eV': -4}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                analyze.build_entries_from_local({**self.energies, 'Mg_2at': bad}, self.info)

    @unittest.skipIf(setup is None, SCIENCE_ERROR)
    def test_normalize_and_download_mpid(self):
        for value in ('mp-1265-r2SCAN', 'mp-1265-GGA+U', 'mp-1265-GGA',
                      {'identifier': 'mp-1265'}, types.SimpleNamespace(identifier='mp-1265')):
            self.assertEqual(setup.normalize_material_id(value), 'mp-1265')
        fake = MagicMock()
        with patch.dict(sys.modules, {'mp_api.client': types.SimpleNamespace(MPRester=fake)}):
            setup.download_structure('mp-1265-r2SCAN', 'unused')
        fake.return_value.__enter__.return_value.get_structure_by_material_id.assert_called_once_with('mp-1265')

    def test_safe_extract(self):
        archive = self.path / 'result.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('workdir/OUTCAR', 'test')
        results.extract(archive, self.path / 'download')
        self.assertTrue((self.path / 'download/workdir/OUTCAR').exists())
        with self.assertRaises(ValueError):
            results.extract(archive, self.path / 'download')

    def test_zip_traversal_rejected(self):
        archive = self.path / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('../outside.txt', 'bad')
        with self.assertRaises(ValueError):
            results.extract(archive, self.path / 'download')
        self.assertFalse((self.path / 'download').exists())

    def test_collect_tagged_energies_atomically(self):
        for name in self.energies:
            out = self.path / 'results' / (name + '_static') / 'download/workdir/OUTCAR'
            out.parent.mkdir(parents=True)
            out.write_text('NIONS = 8\n energy(sigma->0) = -40.0\n', encoding='utf-8')
        records = results.collect(self.path)
        self.assertEqual(records['O2_8at']['basis'], 'cell')
        self.assertEqual(records['O2_8at']['natoms'], 8)
        before = (self.path / 'energies.json').read_bytes()
        out.write_text('incomplete', encoding='utf-8')
        with self.assertRaises(ValueError):
            results.collect(self.path)
        self.assertEqual(before, (self.path / 'energies.json').read_bytes())

    def test_evidence_encoding(self):
        file = self.path / 'result.json'
        file.write_text('{"status":"在凸包上"}', encoding='utf-8-sig')
        self.assertEqual(evidence.read_json(file)['status'], '在凸包上')
        file.write_bytes('{"status":"在凸包上"}'.encode('gbk'))
        self.assertIn('_error', evidence.read_json(file))

    @unittest.skipIf(analyze is None, SCIENCE_ERROR)
    def test_utf8_analysis_output(self):
        (self.path / 'energies.json').write_text(json.dumps(self.energies), encoding='utf-8')
        proc = subprocess.run([sys.executable, '-I', str(SKILLS / 'vasp-thermodynamic-stability/scripts/analyze_stability.py'),
                               '--workdir', str(self.path)], capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf-8', errors='replace'))
        self.assertIn('在凸包上', evidence.read_json(self.path / 'stability_result.json')['status'])

    def test_renderer_cli_portable(self):
        renderer = SKILLS / 'figure-spec/scripts/figure_renderer.py'
        spec = self.path / 'spec.json'
        spec.write_text(json.dumps({'nodes': [{'id': 'a', 'label': '测试', 'x': 100, 'y': 100}]}), encoding='utf-8')
        output = self.path / 'result.svg'
        for args in (['schema'], ['validate', str(spec)], ['render', str(spec), '--output', str(output)]):
            proc = subprocess.run([sys.executable, '-I', str(renderer), *args], cwd=self.path, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('测试', output.read_text(encoding='utf-8'))

    def test_readiness_load_failure(self):
        fake = types.SimpleNamespace(CrystalGenerator=MagicMock())
        with patch.dict(sys.modules, {'mattergen.generator': fake}), \
                patch.object(engine, '_checkpoint_info', side_effect=ValueError('bad checkpoint')):
            answer = engine.check_model_readiness(model_name='chemical_system')
        self.assertFalse(answer['ready'])
        self.assertIn('bad checkpoint', answer['error'])
        fake.CrystalGenerator.assert_not_called()

    def test_readiness_success_does_not_sample(self):
        generator = MagicMock()
        generator.model.diffusion_module.model.cond_fields_model_was_trained_on = ['chemical_system']
        fake = types.SimpleNamespace(CrystalGenerator=MagicMock(return_value=generator))
        with patch.dict(sys.modules, {'mattergen.generator': fake}), \
                patch.object(engine, '_checkpoint_info', return_value=object()), \
                patch.object(engine, '_get_module', return_value=object()):
            answer = engine.check_model_readiness(model_name='chemical_system', conditions={'chemical_system': 'Mg-O'})
        self.assertTrue(answer['ready'])
        self.assertFalse(answer['sampling_performed'])
        generator.generate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
