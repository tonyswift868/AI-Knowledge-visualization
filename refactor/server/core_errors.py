from uuid import uuid4
class AppError(Exception):
    def __init__(self,code,message,status_code=400,details=None):self.code,self.message,self.status_code,self.details=code,message,status_code,details or {}
def app_error_body(code,message,details=None,request_id=None):return {'error':{'code':code,'message':message,'details':details or {},'request_id':request_id or uuid4().hex}}
