"""Bounded matched Command Code pilot, preserving its normal selected model.

Fixed synthetic labels are independent of Jev. Two development and two held-out
cases are run both ways with alternating order. No pruning setting is changed.
This small pilot cannot establish general accuracy or production savings.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jev_decision.credentials import load_api_key  # noqa: E402
from jev_decision.policy import sanitize_excerpt  # noqa: E402


def run(directory):
    directory.mkdir(parents=True, exist_ok=True)
    key = load_api_key()
    rows = []
    cases = [('dev_failure','development',True,1), ('dev_success','development',False,0),
             ('held_failure','held_out',True,2), ('held_success','held_out',False,0)]
    for index, (identity, split, failure, code) in enumerate(cases):
        source = 'tests/test_' + identity + '.py:37'
        fact = source + (' FAILED: expected 4, observed 5' if failure else ' PASSED: expected 4, observed 4')
        lines = ['Recorded execution: '+identity] + ['debug cache observation '+str(i) for i in range(1,145)]
        lines.insert(75, fact)
        lines += [('1 failed' if failure else '1 passed'), 'exit code '+str(code)]
        raw = '\n'.join(lines)+'\n'
        artifact = directory / (identity+'.log')
        artifact.write_text(raw,encoding='utf-8')
        expected = {'failure':failure,'exit_code':code,'source':source,'expected':4,'observed':5 if failure else 4}
        modes = ['disabled','enabled'] if index % 2 == 0 else ['enabled','disabled']
        for mode in modes:
            route = ('Read the saved log using your normal read tool. Do not call any Jev tool.' if mode=='disabled' else
                     'Read the saved log only through the registered Jev MCP tool jev_read_evidence, once, with goal: identify the recorded test outcome and source details. Do not enable pruning. Keep all original evidence.')
            prompt = ('Bounded integration measurement. Do not delegate or modify settings/files. '+route+
                      ' The absolute log path is '+str(artifact)+'. Return only one JSON object with keys failure (boolean), exit_code (integer), source (exact file:line), expected (integer), observed (integer), based on the recorded execution. No extra fields or prose.')
            command = "$jevPilotPrompt = @'\n"+prompt+"\n'@\ncmdc --no-auto-update --no-session --skip-onboarding --max-turns 3 --output-format json -p $jevPilotPrompt"
            start=time.perf_counter()
            process=subprocess.Popen(['pwsh.exe','-NoProfile','-NonInteractive','-Command',command],cwd=directory,
                stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                out,err=process.communicate(timeout=90)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],capture_output=True,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                out,err=process.communicate(timeout=10)
            elapsed=(time.perf_counter()-start)*1000
            text=sanitize_excerpt(out.decode('utf-8','replace'),secrets=(key,) if key else ())
            (directory/(identity+'-'+mode+'.jsonl')).write_text(text,encoding='utf-8')
            events=[]
            for line in text.splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
            final=next((event for event in reversed(events) if event.get('type')=='result'),{})
            final_text=final.get('finalText','')
            answer=None
            try:
                answer=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '',final_text.strip()))
            except ValueError:
                pass
            tool_events=[event['event'] for event in events if event.get('event',{}).get('type')=='tool_completed']
            jev_tools=[event for event in tool_events if event.get('toolName','').startswith('mcp__jev__')]
            jev_result=None
            if jev_tools:
                for item in jev_tools[0].get('result',[]):
                    if item.get('type')=='text':
                        try:
                            jev_result=json.loads(item['text'])
                        except ValueError:
                            pass
            stats=(jev_result or {}).get('stats',{})
            usage=final.get('usage',{})
            models=sorted({event['event']['model'] for event in events if event.get('event',{}).get('type')=='model_request_end'})
            route_valid=(len(jev_tools)==0 and bool(tool_events)) if mode=='disabled' else (len(jev_tools)==1 and stats.get('source')=='provider' and stats.get('status')=='ok')
            row={'case':identity,'split':split,'mode':mode,'model':models,'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                 'route_valid':route_valid,'correct':answer==expected,'expected':expected,'answer':answer,
                 'total_latency_ms':elapsed,'primary_input_tokens':usage.get('inputTokens'),
                 'primary_output_tokens':usage.get('outputTokens'),'primary_cache_read_tokens':usage.get('cacheReadTokens'),
                 'jev_usage':stats.get('usage'),'jev_latency_ms':stats.get('latency_ms'),'jev_status':stats.get('status'),
                 'fallback':mode=='enabled' and not route_valid,
                 'retained_required_evidence':all(str(value) in (jev_result or {}).get('output','') for value in [source,'exit code '+str(code),fact]) if mode=='enabled' else None,
                 'original_preserved':artifact.read_text(encoding='utf-8')==raw,'exit_code':process.returncode}
            rows.append(row)
            print(json.dumps({'case':identity,'mode':mode,'route_valid':route_valid,'correct':row['correct']}),flush=True)
    return {'kind':'small_matched_harness_pilot','harness':'Command Code','rows':rows,
            'automatic_pruning_enabled':False,'independent_labels':'Fixed synthetic execution facts, specified before inference.',
            'limits':['Four synthetic cases; two development and two held-out.','Alternating order reduces but does not eliminate cache and latency effects.',
                      'Primary input tokens include cache reads; provider invoice unverified.','Do not generalize this pilot or enable pruning from these results alone.']}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--workdir',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    report=run(Path(args.workdir).resolve())
    Path(args.output).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
