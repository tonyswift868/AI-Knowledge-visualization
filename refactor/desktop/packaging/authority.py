"""Read literal app identity and public signing keys from frozen bytecode."""
import dis


def literal_authority(code):
    instructions=list(dis.get_instructions(code));result={}
    for index,item in enumerate(instructions):
        if item.opname!='STORE_NAME' or item.argval not in ('APP_ID','PUBLIC_KEYS'):continue
        if item.argval=='APP_ID':
            previous=instructions[index-1]
            if previous.opname!='LOAD_CONST' or not isinstance(previous.argval,str):raise ValueError('nonliteral app identity')
            result['APP_ID']=previous.argval
        else:
            build=instructions[index-1]
            if build.opname!='BUILD_MAP' or not 1<=build.arg<=16:raise ValueError('literal release keys required')
            values=instructions[index-1-2*build.arg:index-1]
            if any(v.opname!='LOAD_CONST' for v in values):raise ValueError('nonliteral release keys')
            result['PUBLIC_KEYS']={values[i].argval:values[i+1].argval for i in range(0,len(values),2)}
    if set(result)!={'APP_ID','PUBLIC_KEYS'}:raise ValueError('compiled release authority missing')
    if any(not isinstance(k,str) or type(v) is not bytes or len(v)!=32 for k,v in result['PUBLIC_KEYS'].items()):
        raise ValueError('invalid compiled release keys')
    return result
