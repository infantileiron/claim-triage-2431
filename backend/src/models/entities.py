from sqlalchemy import Column,Integer,String,Float,JSON,Text,ForeignKey,Boolean,UniqueConstraint
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True);password=Column(String(200));role=Column(String(20))
class Claim(Base):
 __tablename__='claims'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);amount=Column(Float);distance=Column(Float);holiday=Column(Boolean);label=Column(Integer,nullable=True);note=Column(Text,default='')
class Training(Base):
 __tablename__='trainings'
 id=Column(Integer,primary_key=True);name=Column(String(100));threshold=Column(Float);state=Column(String(20),default='queued');result=Column(JSON);error=Column(Text,default='');is_active=Column(Boolean,default=False,nullable=False)
class Review(Base):
 __tablename__='reviews'
 __table_args__=(UniqueConstraint('training_id','claim_id',name='uq_review_source'),)
 id=Column(Integer,primary_key=True);training_id=Column(Integer,ForeignKey('trainings.id'));claim_id=Column(Integer,ForeignKey('claims.id'));score=Column(Float);decision=Column(String(30),default='pending');note=Column(Text,default='');status=Column(String(20),default='draft');superseded_by=Column(Integer,ForeignKey('comparisons.id'),nullable=True)
class Comparison(Base):
 __tablename__='comparisons'
 id=Column(Integer,primary_key=True)
 baseline_id=Column(Integer,ForeignKey('trainings.id'))
 candidate_id=Column(Integer,ForeignKey('trainings.id'))
 state=Column(String(20),default='queued')
 reject_reason=Column(Text,default='')
 error=Column(Text,default='')
 result=Column(JSON)
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
