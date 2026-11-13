"""Render startup: private secret file plus the platform-provided canonical host."""
import json
import os
from pathlib import Path
import re

SECRET_KEYS = {'PR_DATABASE_URL','PR_SECRET_KEY','PR_SUPABASE_SECRET_KEY',
               'PR_SUPABASE_URL','PR_STORAGE_BUCKET','PR_FILE_STORAGE'}


def configure_environment():
    path=os.environ.get('PR_SECRET_FILE')
    if path:
        try:
            raw=Path(path).read_bytes()
            if len(raw)>65536:raise ValueError()
            values=json.loads(raw)
            if not isinstance(values,dict) or not values.keys()<=SECRET_KEYS:
                raise ValueError()
            if any(not isinstance(v,str) or not v for v in values.values()):
                raise ValueError()
        except (OSError,ValueError,UnicodeError):
            raise ValueError('Render secret file is missing or invalid.') from None
        for key,value in values.items():os.environ.setdefault(key,value)
    host=os.environ.get('RENDER_EXTERNAL_HOSTNAME','')
    if not os.environ.get('PR_TRUSTED_HOSTS'):
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.onrender\.com',host):
            raise ValueError('Set PR_TRUSTED_HOSTS or provide a valid Render service hostname.')
        os.environ['PR_TRUSTED_HOSTS']=host
    if not os.environ.get('PR_PUBLIC_ORIGIN'):
        hosts=os.environ['PR_TRUSTED_HOSTS'].split(',')
        if len(hosts)!=1:raise ValueError('Set PR_PUBLIC_ORIGIN explicitly for multiple trusted hosts.')
        os.environ['PR_PUBLIC_ORIGIN']='https://'+hosts[0]


def main():
    configure_environment()
    from .serve import main as serve
    serve()


if __name__=='__main__':main()
