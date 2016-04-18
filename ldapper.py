import time
import bonsai

import logging, prog
log = logging.getLogger(prog.name)

class AsyncJob:
    def __init__(self, msgid, callback, args, kwargs):
        self.msgid = msgid
        self.callback = callback
        self.args = args
        self.kwargs = kwargs

    def __repr__(self):
        return "AsyncJob({} {} {} {})".format(self.msgid, self.callback, self.args, self.kwargs)


class Connection:
    def __init__(self, config):
        self._config = config
        self._client = bonsai.LDAPClient(self._config['url'])
        self._client.set_credentials("SIMPLE", (self._config['binddn'], self._config['password']))
        self._connection = bonsai.LDAPConnection(self._client, is_async=True)
        self._jobs = list()

    def open(self, callback, *args, **kwargs):
        msgid = self._connection.open()
        if callback is None:
            self._wait(msgid)
            return
        job = AsyncJob(msgid, callback, args, kwargs)
        self._jobs.append(job)

    def close(self):
        if hasattr(self, '_connection'): 
            self._connection.close()

    def __del__(self):
        self.close()

    def fileno(self):
        return self._connection.fileno()

    def poll(self):
        '''Handle async jobs ready to be processed.'''
        i = 0
        log.debug("ldap jobs list: {}".format(self._jobs))
        for job in self._jobs[:]:  # iterate shallow copy so we can delete items while looping it
            job_result = self._connection.get_result(job.msgid)
            log.debug("LDAP job result: {}".format(job_result))
            try:
                if job_result:
                    log.debug("Calling back: {} {} {}".format(job.callback, job.args, job.kwargs))
                    job.callback(job_result, *job.args, **job.kwargs)
            finally:
                log.debug("Deleting job #{} for msgid={}".format(i, job.msgid,))
                del self._jobs[i]
            i += 1

    def _wait(self, msgid):
        i = 0
        result = None
        while result is None:
            time.sleep(0.1)
            i += 1
            log.debug("_wait({}): {} times".format(msgid, i))
            result = self._connection.get_result(msgid)
        return result 

    def search(self, base, scope, filter, attrs, callback, *args, **kwargs):
        log.debug("LDAP search: {} {} {} {}".format(base, scope, filter, attrs))
        msgid = self._connection.search(base, scope, filter, attrs)
        job = AsyncJob(msgid, callback, args, kwargs)
        log.debug("search job: {}".format(job))
        self._jobs.append(job)


class MonitorSubscriber:
    def __init__(self, callback, ldapattr, interval=1):
        self.callback = callback
        self.ldapattr = ldapattr
        self.interval = interval
        self.value = None # FIXME: todo; dont call back if value not changed
   
    def __repr__(self):
        return "MonitorSubscriber({}, {})".format(self.ldapattr, self.interval)


class CNMonitor(Connection):
    def __init__(self, *args, **kwargs):
        self.subscriptions = dict()
        super().__init__(*args, **kwargs)

    def subscribe(self, callback, ldapbase, ldapattr, interval=1):
        subs = MonitorSubscriber(callback, ldapattr, interval)
        self.subscriptions.setdefault(ldapbase, list()).append(subs)
        log.debug("subscriptions: {}".format(self.subscriptions))

    def unsubscribe(self, callback, ldapbase):
        subs = self.subscriptions.get(ldapbase)
        if subs is None:
            return
        if len(subs) <= 1:
            del self.subscriptions[ldapbase]
            return
        i = 0
        for sub in subs[:]:
            if sub.callback == callback:
                del subs[i]
                return
            i += 1

    def update(self, ticks):
        for ldapbase, subs in self.subscriptions.items():
            log.debug("iter subscriptions: {} -- {}".format(ldapbase, subs))
            for sub in subs:
                if ticks % sub.interval == 0:
                    log.debug("submitting search job: {}".format(sub))
                    self.search(ldapbase + ",cn=monitor", 0, '', ['+'], self.dispatch_result, sub.callback, sub.ldapattr)

    def dispatch_result(self, result, callback, ldapattr):
        log.debug("result: {}".format(repr(result)))
        callback(result[0][ldapattr][0])

