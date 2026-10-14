"""Read-only Mitosis static-art index. Never reads account, locale, or save tables."""
import math
from pathlib import Path
import re
import urllib.parse


class HaxeData:
    """Bounded, data-only subset of Haxe serialization used by the static file table.

    No class instantiation, callbacks, code execution, or general database editing.
    """
    def __init__(self, data):
        self.data=data.decode('utf-8')
        self.pos=0
        self.strings=[]
        self.objects=[]
        self.nodes=0

    def number(self, floating=False):
        match=re.match(r'[-+0-9.eE]+' if floating else r'-?\d+', self.data[self.pos:])
        if not match: raise ValueError('Invalid serialized number')
        self.pos+=len(match[0])
        return float(match[0]) if floating else int(match[0])

    def value(self, depth=0):
        self.nodes+=1
        if depth>60 or self.nodes>100000: raise ValueError('Cache table exceeds parser limits')
        tag=self.data[self.pos];self.pos+=1
        if tag=='n':return None
        if tag=='t':return True
        if tag=='f':return False
        if tag=='z':return 0
        if tag=='i':return self.number()
        if tag=='d':return self.number(True)
        if tag=='y':
            size=self.number()
            if size<0 or size>8*1024*1024 or self.data[self.pos]!=':': raise ValueError('Invalid serialized string')
            self.pos+=1
            value=urllib.parse.unquote(self.data[self.pos:self.pos+size]);self.pos+=size
            self.strings.append(value);return value
        if tag=='R':return self.strings[self.number()]
        if tag=='r':return self.objects[self.number()]
        if tag=='o':
            value={};self.objects.append(value)
            while self.data[self.pos]!='g':
                key=self.value(depth+1)
                if not isinstance(key,str):raise ValueError('Invalid object key')
                value[key]=self.value(depth+1)
            self.pos+=1;return value
        if tag=='q':
            value={};self.objects.append(value)
            while self.data[self.pos]!='h':
                if self.data[self.pos]!=':':raise ValueError('Invalid integer map')
                self.pos+=1;key=self.number();value[key]=self.value(depth+1)
            self.pos+=1;return value
        if tag=='a':
            value=[];self.objects.append(value)
            while self.data[self.pos]!='h':
                if self.data[self.pos]=='u':
                    self.pos+=1;n=self.number()
                    if n<0 or n>10000:raise ValueError('Invalid null run')
                    value.extend([None]*n)
                else:value.append(self.value(depth+1))
            self.pos+=1;return value
        raise ValueError('Unsupported cache field type: '+tag)


def read_table(path):
    data=Path(path).read_bytes()
    if len(data)>8*1024*1024 or data[:11]!=b'\x09\x00MITOSISDB':raise ValueError('Not a supported Mitosis cache table')
    try:return HaxeData(data[16:]).value()
    except (IndexError,KeyError,RecursionError) as exc:raise ValueError('Malformed cache table') from exc


def visual_names(cache):
    table=Path(cache)/'StaticFilesTbl_0.mtdb'
    if not table.is_file():return {}
    data=read_table(table)
    output={}
    for row in data.get('rows',{}).values():
        if not isinstance(row,dict):continue
        path=row.get('FilePath','')
        if re.fullmatch(r'storage:/[a-f0-9]{32}\.dat',path):
            url=urllib.parse.urlsplit(row.get('Url',''))
            output[path.split('/')[-1]]=urllib.parse.unquote(url.path).lstrip('/')
    return output
