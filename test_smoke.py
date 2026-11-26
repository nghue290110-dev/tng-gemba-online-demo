import os, tempfile, sys
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

with tempfile.TemporaryDirectory() as tmp:
    os.environ['DATA_DIR']=tmp
    os.environ['UPLOAD_DIR']=tmp+'/uploads'
    os.environ['ADMIN_EMAIL']='admin@example.com'
    os.environ['ADMIN_PASSWORD']='safeDemoPassword123!'
    sys.path.insert(0,os.path.dirname(__file__))
    from app.server import app
    admin=TestClient(app)
    assert admin.get('/api/me').status_code==401
    res=admin.post('/api/login',json={'email':'admin@example.com','password':'safeDemoPassword123!'})
    assert res.status_code==200,res.text
    for code,name in [('A1','Manager A'),('B1','Manager B')]:
        res=admin.post('/api/users',json={'code':code,'name':name,'email':code.lower()+'@example.com','branch':'Bông','role':'manager','password':'testPassword234!'})
        assert res.status_code==200,res.text
    people=admin.get('/api/users').json();a=next(p for p in people if p['code']=='A1');b=next(p for p in people if p['code']=='B1')
    A=TestClient(app);B=TestClient(app)
    for client,code in ((A,'a1'),(B,'b1')):
        r=client.post('/api/login',json={'email':code+'@example.com','password':'testPassword234!'})
        assert r.status_code==200,r.text
    due=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
    r=A.post('/api/gembas',json={'branch':'Bông','problem':'Lỗi thêu test','deadline':due,'assignees':[b['id']], 'solution':'Khắc phục'})
    assert r.status_code==200,r.text
    gid=r.json()['id']
    assert len(B.get('/api/gembas').json())==1
    assert B.post(f'/api/gembas/{gid}/review',json={'passed':True}).status_code==403
    assert A.post(f'/api/gembas/{gid}/review',json={'passed':True}).status_code==400
    detail=B.get('/api/gembas/'+str(gid)).json()
    aid=detail['assignments'][0]['id']
    r=B.post(f'/api/assignments/{aid}/done',json={'done':True,'note':'Hoàn thiện'})
    assert r.status_code==200,r.text
    assert A.get(f'/api/gembas/{gid}').json()['status']=='awaiting_review'
    r=A.post(f'/api/gembas/{gid}/review',json={'passed':False,'note':'Không đạt thử nghiệm'})
    assert r.status_code==200,r.text
    assert r.json()['status']=='rework'
    pigs=A.get('/api/pig').json();assert len(pigs)==1,pigs
    assert pigs[0]['reason']=='failed'
    assert B.post(f'/api/assignments/{aid}/done',json={'done':True}).status_code==200
    assert A.post(f'/api/gembas/{gid}/review',json={'passed':True}).status_code==200
    assert A.get(f'/api/gembas/{gid}').json()['status']=='passed'
    # late task
    due=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()
    res=A.post('/api/gembas',json={'branch':'Bông','problem':'Quá hạn test','deadline':due,'assignees':[b['id']]})
    assert res.status_code==200,res.text
    pigs=B.get('/api/pig').json();assert any(x['reason']=='overdue' for x in pigs),pigs
    assert admin.post('/api/reset-demo',json={'confirmation':'XOA DU LIEU DEMO'}).status_code==200
    assert admin.get('/api/gembas').json()==[]
    print('PASS: login/roles, shared records, assignee completion, creator review, fail→PIG, overdue→PIG, admin reset')
