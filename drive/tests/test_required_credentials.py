"""Import actual production settings in a fresh interpreter, without DB/network."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
KEYS=('SECRET_KEY','POSTGRES_PASSWORD','AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY')


class RequiredCredentialTests(unittest.TestCase):
    def settings(self, changes=None, missing=()):
        env={k:v for k,v in os.environ.items() if k not in (*KEYS,'JWT_SECRET_KEY','USE_S3_STORAGE','DJANGO_SETTINGS_MODULE')}
        env.update(SECRET_KEY=secrets.token_urlsafe(32),POSTGRES_PASSWORD=secrets.token_urlsafe(32),USE_S3_STORAGE='0',DEBUG='0')
        env.update(changes or {})
        for key in missing:env.pop(key,None)
        result=subprocess.run([sys.executable,'-c',"import json; from drivemanager import settings as s; print(json.dumps({'secret':s.SECRET_KEY,'password':s.DATABASES['default']['PASSWORD'],'jwt':s.JWT_SECRET_KEY,'s3':getattr(s,'AWS_ACCESS_KEY_ID',None),'s3secret':getattr(s,'AWS_SECRET_ACCESS_KEY',None)}))"],cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
        return result

    def test_missing_and_blank_database_password_refuse_actual_import(self):
        for value in (None,'',' \t '):
            with self.subTest(kind='missing' if value is None else 'blank'):
                r=self.settings(missing=('POSTGRES_PASSWORD',)) if value is None else self.settings({'POSTGRES_PASSWORD':value})
                self.assertNotEqual(r.returncode,0,'production settings accepted missing/blank DB credential')
                self.assertIn('POSTGRES_PASSWORD must be configured',r.stderr)

    def test_signing_configuration_still_refuses_missing_or_blank(self):
        for value in (None,'',' \t '):
            r=self.settings(missing=('SECRET_KEY',)) if value is None else self.settings({'SECRET_KEY':value})
            self.assertNotEqual(r.returncode,0);self.assertIn('SECRET_KEY must be configured',r.stderr)

    def test_configured_signing_and_database_bytes_preserved(self):
        key='  '+secrets.token_urlsafe(32)+'  ';password='  '+secrets.token_urlsafe(32)+'  '
        r=self.settings({'SECRET_KEY':key,'POSTGRES_PASSWORD':password});self.assertEqual(r.returncode,0,r.stderr)
        value=json.loads(r.stdout);self.assertEqual(value['secret'],key);self.assertEqual(value['password'],password);self.assertEqual(value['jwt'],key)

    def test_enabled_S3_requires_explicit_identity_and_secret(self):
        configured={'USE_S3_STORAGE':'1','AWS_ACCESS_KEY_ID':secrets.token_urlsafe(24),'AWS_SECRET_ACCESS_KEY':secrets.token_urlsafe(32)}
        for key in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY'):
            for value in (None,'',' \t '):
                r=self.settings(configured,missing=(key,)) if value is None else self.settings({**configured,key:value})
                self.assertNotEqual(r.returncode,0,'enabled storage accepted missing/blank credential')
                self.assertIn(key+' must be configured',r.stderr)
        r=self.settings(configured);self.assertEqual(r.returncode,0,r.stderr)
        value=json.loads(r.stdout);self.assertEqual(value['s3'],configured['AWS_ACCESS_KEY_ID']);self.assertEqual(value['s3secret'],configured['AWS_SECRET_ACCESS_KEY'])

    def test_explicit_blank_JWT_key_refuses_and_configured_value_is_preserved(self):
        for value in ('',' \t '):
            r=self.settings({'JWT_SECRET_KEY':value})
            self.assertNotEqual(r.returncode,0);self.assertIn('JWT_SECRET_KEY must be configured',r.stderr)
        explicit=secrets.token_urlsafe(32)
        r=self.settings({'JWT_SECRET_KEY':explicit});self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(json.loads(r.stdout)['jwt'],explicit)

    def test_local_storage_does_not_require_inactive_S3_credentials(self):
        r=self.settings(missing=('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY'))
        self.assertEqual(r.returncode,0,r.stderr);self.assertIsNone(json.loads(r.stdout)['s3'])


if __name__=='__main__':unittest.main()
