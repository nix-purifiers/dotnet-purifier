# SPDX-License-Identifier: MIT
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest


def load(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/(name+'.py'))
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


sampler=load('resource-sampler')
controller=load('controller')


class NoSuchProcess(Exception): pass
class AccessDenied(Exception): pass


class ResourceSampler(unittest.TestCase):
    def module(self,error=None):
        calls=[]
        def process(pid):
            calls.append(pid)
            def memory():
                if error: raise error
                return types.SimpleNamespace(rss=123456)
            return types.SimpleNamespace(ppid=lambda:7,memory_info=memory)
        return types.SimpleNamespace(pids=lambda:[1,11,12],Process=process,
                                     NoSuchProcess=NoSuchProcess),calls

    def test_only_owned_group_is_queried_and_rss_stays_bytes(self):
        module,calls=self.module()
        rows=sampler.owned_rows(module,11,getpgid=lambda pid:11 if pid==12 else pid)
        self.assertEqual(calls,[11,12])
        self.assertEqual(rows,[dict(pid=11,parent=7,group=11,rssBytes=123456),
                               dict(pid=12,parent=7,group=11,rssBytes=123456)])

    def test_exit_race_is_skipped_but_access_denied_fails(self):
        for error in (ProcessLookupError(),NoSuchProcess()):
            module,_=self.module(error)
            self.assertEqual(sampler.owned_rows(module,11,getpgid=lambda pid:11),[])
        module,_=self.module(AccessDenied())
        with self.assertRaises(AccessDenied):
            sampler.owned_rows(module,11,getpgid=lambda pid:11)

    def test_membership_is_rechecked_after_memory_query(self):
        module,_=self.module()
        module.pids=lambda:[11]
        groups=iter([11,20])
        self.assertEqual(sampler.owned_rows(module,11,getpgid=lambda pid:next(groups)),[])

    def result(self):
        identity=dict(path='/declared/psutil/provider',sha256='a'*64)
        provider=dict(version='7.2.2',module=identity,extension=identity,pythonExecutable=sys.executable)
        return dict(provider=provider,rows=[dict(pid=11,parent=7,group=11,rssBytes=123456)])

    def call(self,value,exit_code=0,stop=None):
        calls=[]
        def run(argv,cwd,env,**bounds):
            calls.append((argv,bounds))
            return dict(argv=argv,exitCode=exit_code,stopReason=stop,stderr='bounded error',output=json.dumps(value))
        result=controller.sample_owned(Path('/helper'),11,run,Path('/cwd'),{},
                                       site=Path('/declared'),version='7.2.2')
        return result,calls

    def test_generated_sampler_argv_and_bounds_and_provider(self):
        result,calls=self.call(self.result())
        self.assertEqual(result['rows'][0]['rssBytes'],123456)
        self.assertEqual(calls,[([sys.executable,'/helper','--site','/declared','--version','7.2.2',
                                 '--group','11'],dict(timeout=1,limit=262144,separate_stderr=True))])

    def test_failure_stop_foreign_group_duplicate_and_provider_rejected(self):
        for exit_code,stop in ((1,None),(0,'deadline')):
            with self.assertRaises(controller.SampleError): self.call(self.result(),exit_code,stop)
        foreign=self.result(); foreign['rows'][0]['group']=20
        duplicate=self.result(); duplicate['rows']*=2
        wrong_provider=self.result(); wrong_provider['provider']['module']['path']='/foreign/provider'
        no_rss=self.result(); del no_rss['rows'][0]['rssBytes']
        for value in (foreign,duplicate,wrong_provider,no_rss,[]):
            with self.assertRaises(controller.SampleError): self.call(value)


if __name__=='__main__': unittest.main()
