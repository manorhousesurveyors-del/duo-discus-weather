"""Run isolated browser scenarios, terminating each fixture server on completion."""
import argparse,os,socket,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'var'/'architecture-browser';OUT.mkdir(parents=True,exist_ok=True)
parser=argparse.ArgumentParser()
parser.add_argument('--scenario',choices=('healthy','429','timeout'),action='append')
args=parser.parse_args()
for scenario in args.scenario or ('healthy','429','timeout'):
    with tempfile.TemporaryDirectory(prefix='duo-browser-') as tasks:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={**os.environ,'DUO_TEST_SCENARIO':scenario,'DUO_TASK_STORE':tasks,'DUO_OGN_ENABLED':'0','GEMINI_API_KEY':''}
        with (OUT/(scenario+'-server.log')).open('w') as log:
            server=subprocess.Popen([sys.executable,'-m','streamlit','run','scripts/architecture_fixture.py',
                '--server.headless=true','--server.port='+str(port),'--server.fileWatcherType=none',
                '--browser.gatherUsageStats=false'],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                url='http://127.0.0.1:'+str(port)
                deadline=time.monotonic()+30
                while True:
                    try:
                        with urlopen(url+'/_stcore/health',timeout=1) as response:
                            if response.status==200:break
                    except OSError:pass
                    if server.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Fixture did not start: '+scenario)
                    time.sleep(.1)
                subprocess.run([sys.executable,'scripts/verify_architecture.py','--scenario',scenario,'--url',url],cwd=ROOT,env=env,check=True,timeout=240)
            finally:
                server.terminate()
                try:server.wait(timeout=10)
                except subprocess.TimeoutExpired:server.kill();server.wait()
