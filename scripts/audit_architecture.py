"""Reproducible static ownership inventory; excludes vendored third-party code."""
import ast,json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def inventory():
    files=subprocess.check_output(['rg','--files','-g','*.py','-g','*.js','-g','!**/vendor/**'],cwd=ROOT,text=True).splitlines()
    report=[]
    for name in sorted(files):
        text=(ROOT/name).read_text();imports=[];caches=[];state=[]
        if name.endswith('.py'):
            tree=ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):imports.extend(a.name for a in node.names)
                if isinstance(node,ast.ImportFrom) and node.module:imports.append(node.module)
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    caches.extend({'function':node.name,'line':node.lineno,'decorator':ast.unparse(d)} for d in node.decorator_list if 'cache_' in ast.unparse(d))
                if isinstance(node,ast.Subscript) and 'session_state' in ast.unparse(node.value) and isinstance(node.slice,ast.Constant):state.append(str(node.slice.value))
        report.append({'path':name,'lines':len(text.splitlines()),'imports':sorted(set(imports)),
            'cache_decorators':caches,'session_keys':sorted(set(state)),
            'network_lines':[i for i,line in enumerate(text.splitlines(),1) if re.search(r'get_json\(|urlopen\(|requests\.(?:get|post)\(|\bfetch\(',line)]})
    return report

if __name__=='__main__':
    result=inventory();out=ROOT/'docs'/'architecture-inventory.json'
    out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'files':len(result),'python':sum(r['path'].endswith('.py') for r in result),
        'javascript':sum(r['path'].endswith('.js') for r in result),'lines':sum(r['lines'] for r in result)}))
