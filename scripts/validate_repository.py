"""Notebook syntax, local imports, credential/binary guardrails without SDK."""
import ast
import json
from pathlib import Path
import re
import sys
import nbformat

ROOT=Path(__file__).resolve().parents[1]
errors=[]
code_cells=[]
for notebook in sorted((ROOT/'notebooks').glob('*.ipynb')):
    nb=nbformat.read(notebook,as_version=4)
    nbformat.validate(nb)
    notebook_cells=[c for c in nb.cells if c.cell_type=='code']
    if notebook.name=='validate_fubon_futures_intraday.ipynb':
        assert len(notebook_cells)==25
    elif notebook.name=='validate_fubon_futures_historical.ipynb':
        assert 'MAX_PHASE = 1' in '\n'.join(c.source for c in notebook_cells)
    elif notebook.name=='validate_fubon_futures_rollover.ipynb':
        sources='\n'.join(c.source for c in notebook_cells)
        assert 'run_rollover(' in sources and 'MAX_PHASE' not in sources
        assert 'feature/continuous-contract-rollover-validation' in sources
    for i,cell in enumerate(notebook_cells):
        ast.parse(cell.source,filename=f'{notebook.name} cell {i+1}')
        assert cell.execution_count is None and not cell.outputs
    code_cells.extend(notebook_cells)
texts=[]
for path in [*ROOT.glob('src/*.py'),*ROOT.glob('scripts/*.py'),*ROOT.glob('tests/*.py')]:
    text=path.read_text()
    ast.parse(text,filename=str(path))
    texts.append((path,text))
texts += [(Path('notebook'),c.source) for c in code_cells]
for path,text in texts:
    tree=ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom) and node.module and node.module.startswith(('src.','scripts.')):
            assert (ROOT/Path(*node.module.split('.'))).with_suffix('.py').exists(),node.module
        if isinstance(node,ast.Call):
            fn=node.func
            if isinstance(fn,ast.Attribute) and fn.attr in {'place_order','cancel_order','modify_order'}:
                errors.append(f'{path}: forbidden trading method')
    if path.name != 'validate_repository.py' and ('.iterrows(' in text or re.search(r'apply\(.*axis\s*=\s*1',text)):
        errors.append(f'{path}: non-vectorized row operation')
    if re.search(r'\b[A-Z][12][0-9]{8}\b',text):errors.append(f'{path}: possible Taiwan ID')
    if re.search(r'(?:ghp_|github_pat_)[A-Za-z0-9_]{15,}',text):errors.append(f'{path}: possible GitHub token')
    for name in ['FUBON_PERSONAL_ID','FUBON_API_KEY','FUBON_CERT_PASSWORD']:
        if re.search(name+r"\s*=\s*['\"][^'\"]+",text):errors.append(f'{path}: hardcoded secret')
for directory in ['src','scripts','tests','notebooks','config','docs']:
    for p in (ROOT/directory).rglob('*'):
        if p.suffix.lower() in {'.pfx','.p12','.pem','.crt','.cer','.whl','.zip'}:errors.append(f'{p}: forbidden file')
joined='\n'.join(c.source for c in code_cells)
assert 'https://github.com/hh4832/fubon_futures_intraday_data_validation.git' in joined
config=(ROOT/'config/validation_config.yaml').read_text()
assert '/content/drive/MyDrive/00Quant_Research/fubon_futures_intraday_data_validation' in config
if errors:raise RuntimeError('\n'.join(errors))
print('PASS: All Notebook JSON files, intraday 25 code cells, syntax, local imports, output-free cells, paths, read-only operations, credential/binary scan')
