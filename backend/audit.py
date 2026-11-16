"""Durable, bounded-query ingestion audit; names are encrypted at rest."""
import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT NOT NULL, event_id TEXT UNIQUE NOT NULL,
 stamp REAL NOT NULL, key_hash TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ingest_events_run ON ingest_events(run,seq);
CREATE INDEX IF NOT EXISTS ingest_events_expiry ON ingest_events(json_extract(body,'$.expires_at'));
CREATE INDEX IF NOT EXISTS ingest_events_outcome ON ingest_events(run,json_extract(body,'$.outcome'));
CREATE TABLE IF NOT EXISTS ingest_keys(
 run TEXT NOT NULL,key_hash TEXT NOT NULL,first_seq INTEGER,last_seq INTEGER,hits INTEGER NOT NULL,
 PRIMARY KEY(run,key_hash));
CREATE TABLE IF NOT EXISTS ingest_counts(run TEXT,name TEXT,value INTEGER NOT NULL,PRIMARY KEY(run,name));
"""


class Audit:
    def __init__(self, db, cipher, run, *, record_names=False, retention_days=7):
        self.db, self.cipher, self.run = db, cipher, run
        self.record_names, self.retention_days = record_names, retention_days
        self.secret = cipher._signing_key  # Stable per-volume keyed identities, never exposed.
        self.db.executescript(SCHEMA)

    def identity(self, key):
        if isinstance(key,str): key=key.encode()
        return hmac.new(self.secret,key,hashlib.sha256).hexdigest()

    def increment(self, name, count=1):
        self.db.execute('INSERT INTO ingest_counts VALUES(?,?,?) ON CONFLICT(run,name) DO UPDATE SET value=value+excluded.value', (self.run,name,count))

    def intent(self, key, phase='scan', bloom=None):
        raw=key.encode() if isinstance(key,str) else key
        digest=self.identity(raw)
        body={'id':uuid.uuid4().hex,'time':datetime.now(timezone.utc).isoformat(),'phase':phase,
              'expires_at':time.time()+self.retention_days*86400,'key_hash':digest,'key':'key:'+digest[:16],'outcome':'pending','bloom':bloom,'duplicate':False}
        if self.record_names:
            body['encrypted_key']=self.cipher.encrypt(raw).decode()
        with self.db:
            cur=self.db.execute('INSERT INTO ingest_events(run,event_id,stamp,key_hash,body) VALUES(?,?,?,?,?)',
                                (self.run,body['id'],time.time(),digest,json.dumps(body)))
        return cur.lastrowid

    def finish(self, seq, outcome, *, duplicate=False, evidence='', error_type=None):
        row=self.db.execute('SELECT body FROM ingest_events WHERE seq=? AND run=?',(seq,self.run)).fetchone()
        if not row:return
        body=json.loads(row[0])
        if body['outcome']!='pending':return
        body.update(outcome=outcome,duplicate=duplicate,evidence=evidence,error_type=error_type)
        with self.db:
            self.db.execute('UPDATE ingest_events SET body=? WHERE seq=?',(json.dumps(body),seq))
            self.increment('processed');self.increment(outcome)
            if body.get('bloom') is True:self.increment('bloom_positive')
            if body.get('bloom') is True and not duplicate and outcome not in {'error','unknown'}:
                self.increment('positive_without_existing_key')
            if duplicate:
                self.increment('duplicate_events')
                self.db.execute('INSERT INTO ingest_keys VALUES(?,?,?,?,1) ON CONFLICT(run,key_hash) DO UPDATE SET last_seq=excluded.last_seq,hits=hits+1',
                                (self.run,body['key_hash'],seq,seq))

    def recover_pending(self):
        # A lost acknowledgement cannot be classified as a failed write.
        rows=self.db.execute("SELECT seq FROM ingest_events WHERE run=? AND json_extract(body,'$.outcome')='pending'",(self.run,)).fetchall()
        for row in rows:self.finish(row[0],'unknown',evidence='worker-interrupted')

    def public(self, body):
        body=dict(body)
        encrypted=body.pop('encrypted_key',None)
        body['name_recorded']=bool(encrypted)
        if encrypted:
            raw=self.cipher.decrypt(encrypted.encode())
            try:body['key']=raw.decode('utf-8')
            except UnicodeDecodeError:
                body['key']='base64:'+base64.b64encode(raw).decode()
                body['encoding']='base64'
        body['origin']='workbench-audit'
        return body

    def events(self, after=0, limit=100, event_id=None, outcome=None, latest=False):
        where='run=? AND seq>?';params=[self.run,after]
        if event_id:where+=' AND event_id=?';params.append(event_id)
        # Do not decode/read the entire event history for pagination.
        if outcome:where+=" AND json_extract(body,'$.outcome')=?";params.append(outcome)
        rows=self.db.execute('SELECT seq,body FROM ingest_events WHERE '+where+(' ORDER BY seq DESC LIMIT ?' if latest else ' ORDER BY seq LIMIT ?'),(*params,limit+1)).fetchall()
        items=[self.public(json.loads(row[1]))|{'seq':row[0]} for row in rows[:limit]]
        return {'items':items,'next_cursor':items[-1]['seq'] if len(rows)>limit and not latest else None}

    def keys(self, after=0,limit=50):
        rows=self.db.execute('SELECT e.seq,e.body,k.hits,k.first_seq FROM ingest_keys k JOIN ingest_events e ON e.seq=k.last_seq WHERE k.run=? AND k.first_seq>? ORDER BY k.first_seq LIMIT ?', (self.run,after,limit+1)).fetchall()
        items=[self.public(json.loads(row[1]))|{'seq':row[0],'hits':row[2],'cursor':row[3]} for row in rows[:limit]]
        return {'items':items,'next_cursor':items[-1]['cursor'] if len(rows)>limit else None}

    def summary(self):
        counts=dict(self.db.execute('SELECT name,value FROM ingest_counts WHERE run=?',(self.run,)))
        counts['unique_duplicate_keys']=self.db.execute('SELECT COUNT(*) FROM ingest_keys WHERE run=?',(self.run,)).fetchone()[0]
        counts['pending']=self.db.execute("SELECT COUNT(*) FROM ingest_events WHERE run=? AND json_extract(body,'$.outcome')='pending'",(self.run,)).fetchone()[0]
        counts['retained_events']=self.db.execute('SELECT COUNT(*) FROM ingest_events WHERE run=?',(self.run,)).fetchone()[0]
        counts['available']=bool(self.db.execute('SELECT 1 FROM ingest_events WHERE run=? LIMIT 1',(self.run,)).fetchone())
        return counts

    def prune(self):
        # Counters survive retention; explicitly expose that detailed names may expire.
        with self.db:
            self.db.execute("DELETE FROM ingest_events WHERE json_extract(body,'$.expires_at')<?",(time.time(),))
