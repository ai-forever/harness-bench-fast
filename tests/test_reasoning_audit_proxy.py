import importlib.util
import json
from pathlib import Path


def load(tmp_path,monkeypatch):
 monkeypatch.setenv('HBF_AUDIT_DIR',str(tmp_path))
 spec=importlib.util.spec_from_file_location('audit_proxy',Path(__file__).parents[1]/'scripts/hbf_reasoning_audit_proxy.py')
 p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p);return p


def test_repair_exact_identity_preserves_history_and_detects_loss(tmp_path,monkeypatch):
 p=load(tmp_path,monkeypatch)
 call={'id':'c1','function':{'name':'Bash','arguments':'{"cmd":"pwd"}'}}
 p.observe('brain',{'reasoning_content':'exact\nthought','tool_calls':[call]})
 body={'messages':[{'role':'assistant','content':None,'tool_calls':[call]}]}
 check=p.prepare(body,'brain',False)[0]
 assert not check['sent_exact'] and 'reasoning_content' not in body['messages'][0]
 check=p.prepare(body,'brain',True)[0]
 assert check['restored'] and body['messages'][0]['reasoning_content']=='exact\nthought'
 assert p.prepare(body,'brain',True)[0]['native_exact']


def test_no_cross_harness_or_tool_signature_injection(tmp_path,monkeypatch):
 p=load(tmp_path,monkeypatch)
 call={'id':'c1','function':{'name':'Bash','arguments':'{}'}}
 p.observe('brain',{'reasoning_content':'private-thought','tool_calls':[call]})
 body={'messages':[{'role':'assistant','tool_calls':[call]}]}
 assert p.prepare(body,'other',True)==[]
 body['messages'][0]['tool_calls'][0]['function']['name']='Different'
 assert p.prepare(body,'brain',True)==[]
 assert 'reasoning_content' not in body['messages'][0]
