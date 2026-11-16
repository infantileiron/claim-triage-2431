import os
import tempfile
_tmp=tempfile.NamedTemporaryFile(suffix='.db',delete=False)
_tmp.close()
os.environ.setdefault('DATABASE_URL','sqlite:///'+_tmp.name)
os.environ.setdefault('REDIS_URL','redis://localhost:6399/15')
os.environ.setdefault('JWT_SECRET','test-secret-3106')
os.environ.pop('SEED_DEMO',None)
import pytest
from fastapi.testclient import TestClient
from src.config.database import Base,engine,Session
from src.main import app
from src.models.entities import User,Claim,Training
from src.utils.security import hash_password
from src.services.business import dump

class FakeRedis:
 def __init__(self):self.pushed=[]
 def rpush(self,key,value):self.pushed.append((key,int(value)));return 1

@pytest.fixture
def redis_stub(monkeypatch):
 stub=FakeRedis()
 import src.routes.api as api_mod
 monkeypatch.setattr(api_mod,'redis_client',lambda:stub)
 import src.worker as worker_mod
 monkeypatch.setattr(worker_mod,'r',stub)
 return stub

@pytest.fixture
def db(redis_stub):
 Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
 with Session() as s:
  for role in ('admin','analyst','viewer'):
   s.add(User(email=role+'@demo.local',role=role,password=hash_password('Demo123!')))
  s.commit()
 yield Session
 Base.metadata.drop_all(engine)

@pytest.fixture
def client(db):
 with TestClient(app) as c:
  yield c

def login(client,role):
 r=client.post('/auth/login',json={'email':role+'@demo.local','password':'Demo123!'})
 assert r.status_code==200,r.text
 return {'Authorization':'Bearer '+r.json()['token']}

@pytest.fixture
def auth(client):
 return {role:login(client,role) for role in ('admin','analyst','viewer')}

def add_claim(s,code,amount=1000.0,distance=100.0,holiday=False,label=None):
 row=Claim(code=code,amount=amount,distance=distance,holiday=holiday,label=label,note='test')
 s.add(row);s.commit();return row

def make_version(s,name,threshold,claims,train_codes,holdout_scores,pending_scores=None,active=False):
 """构造与 analysis.train 同构的 done 版本结果，供配对/切换逻辑消费。"""
 if pending_scores is None:pending_scores={}
 snapshot=[dump(c) for c in claims]
 by_id={c.id:c for c in claims}
 holdout=[{'id':cid,'code':by_id[cid].code,'label':by_id[cid].label,'score':float(score)}
          for cid,score in sorted(holdout_scores.items())]
 pending=[{'id':cid,'code':by_id[cid].code,'score':float(score)}
          for cid,score in sorted(pending_scores.items())]
 result={'snapshot':snapshot,'holdout':holdout,'pending':pending,
         'train_codes':sorted(train_codes),'test_codes':[h['code'] for h in holdout],
         'train_n':len(train_codes),'test_n':len(holdout),'seed':3106,
         'metrics':{},'threshold_comparison':[],'coefficients':[],'scaler_mean':[],
         'label_counts':{},'checksum':'test','warning':'test'}
 row=Training(name=name,threshold=threshold,state='done',result=result,is_active=active)
 s.add(row);s.commit();return row
