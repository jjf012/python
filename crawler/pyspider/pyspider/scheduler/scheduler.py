#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
# Author: Binux<i@binux.me>
#         http://binux.me
# Created on 2014-02-07 17:05:11


import os
import time
import Queue
import logging
from collections import deque

from pyspider.libs import counter
from task_queue import TaskQueue
logger = logging.getLogger('scheduler')


class Scheduler(object):
    UPDATE_PROJECT_INTERVAL = 5*60
    default_schedule = {
            'priority': 0,
            'retries': 3,
            'exetime': 0,
            'age': 30*24*60*60,
            'itag': None,
            }
    LOOP_LIMIT = 1000
    LOOP_INTERVAL = 0.1
    ACTIVE_TASKS = 100
    INQUEUE_LIMIT = 0
    EXCEPTION_LIMIT = 3
    DELETE_TIME = 24*60*60
    
    def __init__(self, taskdb, projectdb, newtask_queue, status_queue, out_queue, data_path = './data', resultdb=None):
        self.taskdb = taskdb
        self.projectdb = projectdb
        self.resultdb = resultdb
        self.newtask_queue = newtask_queue
        self.status_queue = status_queue
        self.out_queue = out_queue
        self.data_path = data_path

        self._send_buffer = deque()
        self._quit = False
        self._exceptions = 0
        self.projects = dict()
        self._force_update_project = False
        self._last_update_project = 0
        self.task_queue = dict()
        self._last_tick = int(time.time())

        self._cnt = {
                "5m": counter.CounterManager(
                    lambda : counter.TimebaseAverageWindowCounter(30, 10)),
                "1h": counter.CounterManager(
                    lambda : counter.TimebaseAverageWindowCounter(60, 60)),
                "1d": counter.CounterManager(
                    lambda : counter.TimebaseAverageWindowCounter(10*60, 24*6)),
                "all": counter.CounterManager(
                    lambda : counter.TotalCounter()),
                }
        self._cnt['1h'].load(os.path.join(self.data_path, 'scheduler.1h'))
        self._cnt['1d'].load(os.path.join(self.data_path, 'scheduler.1d'))
        self._cnt['all'].load(os.path.join(self.data_path, 'scheduler.all'))
        self._last_dump_cnt = 0

    def _load_projects(self):
        self.projects = dict()
        for project in self.projectdb.get_all():
            self._update_project(project)
            logger.debug("project: %s loaded.", project['name'])
        self._last_update_project = time.time()

    def _update_projects(self):
        now = time.time()
        if not self._force_update_project and self._last_update_project + self.UPDATE_PROJECT_INTERVAL > now:
            return
        for project in self.projectdb.check_update(self._last_update_project):
            self._update_project(project)
            logger.debug("project: %s updated.", project['name'])
        self._force_update_project = False
        self._last_update_project = now

    def _update_project(self, project):
        if project['name'] not in self.projects:
            self.projects[project['name']] = {}
        self.projects[project['name']].update(project)
        if not self.projects[project['name']].get('active_tasks', None):
            self.projects[project['name']]['active_tasks'] = deque(maxlen=self.ACTIVE_TASKS)

        # load task queue when project is running and delete task_queue when project is stoped
        if project['status'] in ('RUNNING', 'DEBUG'):
            if project['name'] not in self.task_queue:
                self._load_tasks(project['name'])
            self.task_queue[project['name']].rate = project['rate']
            self.task_queue[project['name']].burst = project['burst']

            # update project runtime info from processing by sending a on_get_info request,
            # result is catched by on_request
            self.send_task({
                'taskid': '_on_get_info',
                'project': project['name'],
                'url': 'data:,_on_get_info',
                'status': self.taskdb.ACTIVE,
                'fetch': {
                    'save': ['min_tick', ],
                    },
                'process': {
                    'callback': '_on_get_info',
                    },
                'project_updatetime': self.projects[project['name']].get('updatetime', 0),
                })
        else:
            if project['name'] in self.task_queue:
                self.task_queue[project['name']].rate = 0
                self.task_queue[project['name']].burst = 0
                del self.task_queue[project['name']]

    scheduler_task_fields = ['taskid', 'project', 'schedule', ]
    def _load_tasks(self, project):
        """
        loading tasks from database
        """
        self.task_queue[project] = TaskQueue(rate=0, burst=0)
        for task in self.taskdb.load_tasks(self.taskdb.ACTIVE, project, self.scheduler_task_fields):
            taskid = task['taskid']
            _schedule = task.get('schedule', self.default_schedule)
            priority = _schedule.get('priority', self.default_schedule['priority'])
            exetime = _schedule.get('exetime', self.default_schedule['exetime'])
            self.task_queue[project].put(taskid, priority, exetime)

        if self.projects[project]['status'] in ('RUNNING', 'DEBUG'):
            self.task_queue[project].rate = self.projects[project]['rate']
            self.task_queue[project].burst = self.projects[project]['burst']
        else:
            self.task_queue[project].rate = 0
            self.task_queue[project].burst = 0

        if project not in self._cnt['all']:
            status_count = self.taskdb.status_count(project)
            self._cnt['all'].value((project, 'success'),
                status_count.get(self.taskdb.SUCCESS, 0))
            self._cnt['all'].value((project, 'failed'),
                status_count.get(self.taskdb.FAILED, 0)+status_count.get(self.taskdb.BAD, 0))
        self._cnt['all'].value((project, 'pending'), len(self.task_queue[project]))

    def task_verify(self, task):
        for each in ('taskid', 'project', 'url', ):
            if each not in task or not task[each]:
                logger.error('%s not in task: %s' % (each, unicode(task)[:200]))
                return False
        if task['project'] not in self.task_queue:
            logger.error('unknow project: %s' % task['project'])
            return False
        return True

    def insert_task(self, task):
        return self.taskdb.insert(task['project'], task['taskid'], task)

    def update_task(self, task):
        return self.taskdb.update(task['project'], task['taskid'], task)

    def put_task(self, task):
        _schedule = task.get('schedule', self.default_schedule)
        self.task_queue[task['project']].put(task['taskid'],
                priority=_schedule.get('priority', self.default_schedule['priority']),
                exetime=_schedule.get('exetime', self.default_schedule['exetime']))

    def send_task(self, task, force=True):
        try:
            self.out_queue.put_nowait(task)
        except Queue.Full:
            if force:
                self._send_buffer.appendleft(task)
            else:
                raise

    def _check_task_done(self):
        cnt = 0
        try:
            while cnt < self.LOOP_LIMIT:
                task = self.status_queue.get_nowait()
                if not self.task_verify(task):
                    continue
                self.task_queue[task['project']].done(task['taskid'])
                task = self.on_task_status(task)
                cnt += 1
        except Queue.Empty:
            pass
        return cnt

    merge_task_fields = ['taskid', 'project', 'url', 'status', 'schedule', 'lastcrawltime']
    def _check_request(self):
        cnt = 0
        try:
            processed_task_cache = set()
            while cnt < self.LOOP_LIMIT:
                task = self.newtask_queue.get_nowait()
                if not self.task_verify(task):
                    continue

                # check _on_get_info result here
                if task['url'] == 'data:,on_get_info':
                    self.projects[task['project']].update(task['fetch'].get('save', {}))
                    logger.info('%s on_get_info %r', task['project'], task['fetch'].get('save', {}))
                    continue

                if self.INQUEUE_LIMIT and len(self.task_queue[task['project']]) >= self.INQUEUE_LIMIT:
                    logger.debug('overflow task %(project)s:%(taskid)s %(url)s', task)
                    continue
                if task['taskid'] in self.task_queue[task['project']]:
                    if not task.get('schedule', {}).get('force_update', False):
                        logger.debug('ignore newtask %(project)s:%(taskid)s %(url)s', task)
                        continue
                cache_key = "%(project)s:%(taskid)s" % task
                if cache_key in processed_task_cache:
                    logger.debug('processed newtask %(project)s:%(taskid)s %(url)s', task)
                    continue

                oldtask = self.taskdb.get_task(task['project'], task['taskid'],
                        fields=self.merge_task_fields)
                if oldtask:
                    task = self.on_old_request(task, oldtask)
                else:
                    task = self.on_new_request(task)
                processed_task_cache.add(cache_key)
                cnt += 1
        except Queue.Empty:
            pass
        return cnt

    def _check_cronjob(self):
        """
        check projects cronjob tick, return True when a new tick is sended
        """
        now = time.time()
        self._last_tick = int(self._last_tick)
        if now - self._last_tick < 1:
            return False
        self._last_tick += 1
        for project in self.projects.itervalues():
            if project['status'] not in ('DEBUG', 'RUNNING'):
                continue
            if project.get('min_tick', 0) == 0:
                continue
            if self._last_tick % int(project['min_tick']) != 0:
                continue
            self.send_task({
                'taskid': '_on_cronjob',
                'project': project['name'],
                'url': 'data:,_on_cronjob',
                'status': self.taskdb.ACTIVE,
                'fetch': {
                    'save': {
                        'tick': self._last_tick,
                        },
                    },
                'process': {
                    'callback': '_on_cronjob',
                    },
                'project_updatetime': self.projects[project['name']].get('updatetime', 0),
                })
        return True

    request_task_fields = ['taskid', 'project', 'url', 'status', 'fetch', 'process', 'track', 'lastcrawltime']
    def _check_select(self):
        while self._send_buffer:
            _task = self._send_buffer.pop()
            try:
                self.out_queue.put_nowait(_task)
            except Queue.Full:
                self._send_buffer.append(_task)
                break

        cnt_dict = dict()
        for project, task_queue in self.task_queue.iteritems():
            # task queue
            self.task_queue[project].check_update()
            cnt = 0
            while cnt < self.LOOP_LIMIT / 10 and not self._send_buffer:
                taskid = task_queue.get()
                if not taskid:
                    break
                task = self.taskdb.get_task(project, taskid, fields=self.request_task_fields)
                if not task:
                    continue

                # inform processor project may updated
                task['project_updatetime'] = self.projects[project].get('updatetime', 0)
                task = self.on_select_task(task)
                cnt += 1
            cnt_dict[project] = cnt
        return cnt_dict

    def _dump_cnt(self):
        self._cnt['1h'].dump(os.path.join(self.data_path, 'scheduler.1h'))
        self._cnt['1d'].dump(os.path.join(self.data_path, 'scheduler.1d'))
        self._cnt['all'].dump(os.path.join(self.data_path, 'scheduler.all'))

    def _try_dump_cnt(self):
        now = time.time()
        if now - self._last_dump_cnt > 60:
            self._last_dump_cnt = now
            self._dump_cnt()

    def _check_delete(self):
        now = time.time()
        for project in self.projects.values():
            if project['status'] != 'STOP':
                continue
            if now - project['updatetime'] < self.DELETE_TIME:
                continue
            if 'delete' not in self.projectdb.split_group(project['group']):
                continue

            logger.warning("deleting project: %s!", project['name'])
            if project['name'] in self.task_queue:
                self.task_queue[project['name']].rate = 0
                self.task_queue[project['name']].burst = 0
                del self.task_queue[project['name']]
            del self.projects[project['name']]
            self.taskdb.drop(project['name'])
            self.projectdb.drop(project['name'])
            if self.resultdb:
                self.resultdb.drop(project['name'])

    def __len__(self):
        return sum((len(x) for x in self.task_queue.itervalues()))

    def quit(self):
        self._quit = True

    def run(self):
        logger.info("loading projects")
        self._load_projects()

        while not self._quit:
            try:
                time.sleep(self.LOOP_INTERVAL)
                self._update_projects()
                self._check_task_done()
                self._check_request()
                while self._check_cronjob():
                    pass
                self._check_select()
                self._check_delete()
                self._try_dump_cnt()
                self._exceptions = 0
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.exception(e)
                self._exceptions += 1
                if self._exceptions > self.EXCEPTION_LIMIT:
                    break
                continue

        logger.info("scheduler exiting...")
        self._dump_cnt()

    def xmlrpc_run(self, port=23333, bind='127.0.0.1', logRequests=False):
        from SimpleXMLRPCServer import SimpleXMLRPCServer

        server = SimpleXMLRPCServer((bind, port), allow_none=True, logRequests=logRequests)
        server.register_introspection_functions()
        server.register_multicall_functions()

        server.register_function(self.quit, '_quit')
        server.register_function(self.__len__, 'size')

        def dump_counter(_time, _type):
            return self._cnt[_time].to_dict(_type)
        server.register_function(dump_counter, 'counter')

        def new_task(task):
            if self.task_verify(task):
                self.newtask_queue.put(task)
                return True
            return False
        server.register_function(new_task, 'newtask')

        def update_project():
            self._force_update_project = True
        server.register_function(update_project, 'update_project')

        def get_active_tasks(project=None, limit=100):
            allowed_keys = set(('taskid', 'project', 'status', 'url', 'lastcrawltime', 'updatetime', 'track', ))

            iters = [iter(x['active_tasks']) for k, x in self.projects.iteritems()\
                    if x and (k == project if project else True)]
            tasks = [next(x, None) for x in iters]
            result = []

            while len(result) < limit and tasks and not all(x is None for x in tasks):
                updatetime, task = t = max(tasks)
                i = tasks.index(t)
                tasks[i] = next(iters[i], None)
                for key in task.keys():
                    if key in allowed_keys:
                        continue
                    del task[key]
                result.append(t)
            return result
        server.register_function(get_active_tasks, 'get_active_tasks')

        server.timeout = 0.5
        while not self._quit:
            server.handle_request()
    
    def on_new_request(self, task):
        task['status'] = self.taskdb.ACTIVE
        self.insert_task(task)
        self.put_task(task)

        project = task['project']
        self._cnt['5m'].event((project, 'pending'), +1)
        self._cnt['1h'].event((project, 'pending'), +1)
        self._cnt['1d'].event((project, 'pending'), +1)
        self._cnt['all'].event((project, 'pending'), +1)
        logger.debug('new task %(project)s:%(taskid)s %(url)s', task)
        return task

    def on_old_request(self, task, old_task):
        now = time.time()

        _schedule = task.get('schedule', self.default_schedule)
        old_schedule = old_task.get('schedule', {})

        restart = False
        if _schedule.get('itag') and _schedule['itag'] != old_schedule.get('itag'):
            restart = True
        elif _schedule.get('age', self.default_schedule['age']) + (old_task['lastcrawltime'] or 0) < now:
            restart = True
        elif _schedule.get('force_update'):
            restart = True

        if not restart:
            logger.debug('ignore newtask %(project)s:%(taskid)s %(url)s', task)
            return

        task['status'] = self.taskdb.ACTIVE
        self.update_task(task)
        self.put_task(task)

        project = task['project']
        self._cnt['5m'].event((project, 'pending'), +1)
        self._cnt['1h'].event((project, 'pending'), +1)
        self._cnt['1d'].event((project, 'pending'), +1)
        if old_task['status'] == self.taskdb.SUCCESS:
            self._cnt['all'].event((project, 'success'), -1).event((project, 'pending'), +1)
        elif old_task['status'] == self.taskdb.FAILED:
            self._cnt['all'].event((project, 'failed'), -1).event((project, 'pending'), +1)
        logger.debug('restart task %(project)s:%(taskid)s %(url)s', task)
        return task

    def on_task_status(self, task):
        try:
            fetchok = task['track']['fetch']['ok']
            procesok = task['track']['process']['ok']
            if task['taskid'] not in self.task_queue[task['project']].processing:
                logging.error('not processing pack: %(project)s:%(taskid)s %(url)s', task)
                return None
        except KeyError, e:
            logger.error("Bad status pack: %s", e)
            return None

        if fetchok and procesok:
            ret = self.on_task_done(task)
        else:
            ret = self.on_task_failed(task)
        self.projects[task['project']]['active_tasks'].appendleft((time.time(), task))
        return ret

    def on_task_done(self, task):
        '''
        called by task_status
        '''
        task['status'] = self.taskdb.SUCCESS
        task['lastcrawltime'] = time.time()
        self.update_task(task)

        project = task['project']
        self._cnt['5m'].event((project, 'success'), +1)
        self._cnt['1h'].event((project, 'success'), +1)
        self._cnt['1d'].event((project, 'success'), +1)
        self._cnt['all'].event((project, 'success'), +1).event((project, 'pending'), -1)
        logger.debug('task done %(project)s:%(taskid)s %(url)s', task)
        return task

    def on_task_failed(self, task):
        '''
        called by task_status
        '''
        old_task = self.taskdb.get_task(task['project'], task['taskid'], fields=['schedule'])
        if old_task is None:
            logging.error('unknow status pack: %s' % task)
            return
        if not task.get('schedule'):
            task['schedule'] = old_task.get('schedule', {})

        retries = task['schedule'].get('retries', self.default_schedule['retries'])
        retried = task['schedule'].get('retried', 0)
        if retried == 0:
            next_exetime = 0
        elif retried == 1:
            next_exetime = 1 * 60 * 60
        else:
            next_exetime = 6 * (2**retried) * 60 * 60

        if retried >= retries:
            task['status'] = self.taskdb.FAILED
            task['lastcrawltime'] = time.time()
            self.update_task(task)

            project = task['project']
            self._cnt['5m'].event((project, 'failed'), +1)
            self._cnt['1h'].event((project, 'failed'), +1)
            self._cnt['1d'].event((project, 'failed'), +1)
            self._cnt['all'].event((project, 'failed'), +1).event((project, 'pending'), -1)
            logger.info('task failed %(project)s:%(taskid)s %(url)s' % task)
            return task
        else:
            task['schedule']['retried'] = retried + 1
            task['schedule']['exetime'] = time.time() + next_exetime
            task['lastcrawltime'] = time.time()
            self.update_task(task)
            self.put_task(task)

            project = task['project']
            self._cnt['5m'].event((project, 'retry'), +1)
            self._cnt['1h'].event((project, 'retry'), +1)
            self._cnt['1d'].event((project, 'retry'), +1)
            #self._cnt['all'].event((project, 'retry'), +1)
            logger.info('task retry %d/%d %%(project)s:%%(taskid)s %%(url)s' % (
                retried, retries), task)
            return task
        
    def on_select_task(self, task):
        logger.debug('select %(project)s:%(taskid)s %(url)s', task)
        self.send_task(task)
        self.projects[task['project']]['active_tasks'].appendleft((time.time(), task))
        return task
