#!/bin/env python
"""
This module maps request to function based on the url and method
"""
import os
import glob
import hashlib
import fcntl
import time
import json
import pickle
import pycurl
import cStringIO
import thread
import logging
from config import config
from cgi import parse_qs

logger = logging.getLogger('disk_mapper')
hdlr = logging.FileHandler('/var/log/disk_mapper.log')
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
hdlr.setFormatter(formatter)
logger.addHandler(hdlr)
logger.setLevel(logging.WARNING)


class DiskMapper:

    def __init__(self, environ, start_response):
        self.mapping_file = '/var/tmp/disk_mapper/host.mapping'
        if environ != None:
            self.environ  = environ
            self.query_string  = environ["QUERY_STRING"]
            self.start_response = start_response
            self.status = '400 Bad Request'
            self.response_headers = [('Content-type', 'text/plain')]

    def forward_request(self):
        self.status = '202 Accepted'
        path = self.environ["PATH_TRANSLATED"]
        request_uri = self.environ["REQUEST_URI"]

        host_name =  path.split("/")[5]
        mapping = self._get_mapping ("host", host_name)
            
        if mapping == False:
            self.status = '400 Bad Request'
            self._start_response()
            return "Host name " + host_name + " not found in mapping."
            
        status = None
        if "primary" in mapping.keys():
            storage_server = mapping["primary"]["storage_server"]
            status = mapping["primary"]["status"]

        if status == "bad" or status == None:
            if "secondary" in mapping.keys():
                storage_server = mapping["secondary"]["storage_server"]
                status = mapping["secondary"]["status"]
                if status == "bad":
                    logger.error("Both primary and secondary are bad disks.")
                    self.status = '412 Precondition Failed'
                    self._start_response()
                    return "Both primary and secondary are bad disks."
            
        url = 'http://' + storage_server + request_uri
        self.status = '302 FOUND'
        self.response_headers.append(("Location", str(url)))
        self._start_response()
        return str(url)

    
    def upload(self):

        self.status = '202 Accepted'
        path = self.environ["PATH_TRANSLATED"]
        request_uri = self.environ["REQUEST_URI"]

        if not self._is_diskmapper_initialized():
            self.initialize_diskmapper()

        host_name =  path.split("/")[5]
        game_id =  path.split("/")[4]

        if not self._is_host_initialized(host_name):
            self.initialize_host(host_name, "primary", game_id)
            self.initialize_host(host_name, "secondary", game_id)

        return self.forward_request()

    def initialize_host(self, host_name, type, game_id, update_mapping=True):
        
        mapping = self._get_mapping("host", host_name)

        skip_storage_server = None
        if mapping != False:
            if type == "primary" and "secondary" in mapping.keys():
                if mapping["secondary"]["status"] != "bad":
                    skip_storage_server =  mapping["secondary"]["storage_server"]

            if type == "secondary" and "primary" in mapping.keys():
                if mapping["primary"]["status"] != "bad":
                    skip_storage_server =  mapping["primary"]["storage_server"]
        
        spare = self._get_spare(type, skip_storage_server)
        if spare == False:
            logger.error(type + " spare not found for " + host_name)
            return False

        spare_server  = spare["storage_server"]
        spare_disk  = spare["disk"]
        spare_config = self._get_server_config(spare_server)
        if spare_config[spare_disk][type] != "spare":
            return False
            #return self.initialize_host(host_name, type, game_id)

        if self._initialize_host(spare_server, host_name, type, game_id, spare_disk, update_mapping) != False:
            if update_mapping == False:
                return spare
            return True

        return False

    def swap_bad_disk(self, storage_servers=None):
        swap_all_disk = True
        if storage_servers == None:
            swap_all_disk = False
            storage_servers = config['storage_server']

        self.swap_disk_thread_count = 0
        for storage_server in storage_servers:
            self.swap_disk_thread_count = self.swap_disk_thread_count + 1
            thread.start_new_thread(self.poll_bad_file, (storage_server, swap_all_disk))

        while self.swap_disk_thread_count > 0:
            pass

    def poll_bad_file(self, storage_server, swap_all_disk=False):
        # TODO try this entire function and reduce thread count
        
        if swap_all_disk == False:
            server_config = self._get_server_config(storage_server)
            if server_config == False:
                logger.error("Failed to get config from storage server: " + storage_server)
                self.swap_disk_thread_count = self.swap_disk_thread_count - 1
                return False

            bad_disks = self._get_bad_disks(storage_server)
            if server_config == False:
                logger.error("Failed to get bad disks form storage server: " + storage_server)
                self.swap_disk_thread_count = self.swap_disk_thread_count - 1
                return False
        else:
            server_config = self._get_mapping("storage_server",storage_server)

        for disk in server_config:
            status = "bad"
            if swap_all_disk == False:
                if disk not in bad_disks:
                    status = "good"

            if status == "bad":
                for type in server_config[disk]:
                    host_name = server_config[disk][type]
                    self._update_mapping(storage_server, disk, type, host_name, status)
                    if host_name != "spare":
                        game_id = host_name.split("-")[0]
                        if type == "primary":
                            cp_from_type = "secondary"
                        elif type == "secondary":
                            cp_from_type = "primary"

                        mapping = self._get_mapping("host", host_name)
                        if mapping == False:
                            logger.error("Failed to get mapping for " + host_name)
                            continue

                        if type in mapping.keys():
                            continue

                        spare = self.initialize_host(host_name, type, game_id, False)
                        
                        if spare == False:
                            logger.error("Failed to swap " + storage_server + ":/" + disk + "/" + type)
                            continue

                        cp_to_server = spare["storage_server"]
                        cp_to_disk = spare["disk"]
                        cp_to_type = type
                        cp_to_file = os.path.join("/", cp_to_disk, cp_to_type, host_name)

                        try:
                            cp_from_server = mapping[cp_from_type]["storage_server"]
                            cp_from_disk = mapping[cp_from_type]["disk"]
                            cp_from_file = os.path.join("/", cp_from_disk, cp_from_type, host_name)
                        except KeyError:
                            continue

                        torrent_url = self._create_torrent(cp_from_server, cp_from_file)
                        if torrent_url == False:
                            logger.error("Failed to get torrent url for " + storage_server + ":" + file)
                            continue

                        if self._start_download(cp_to_server, cp_to_file, torrent_url) == True:
                            self._update_mapping(cp_to_server, cp_to_disk, cp_to_type, host_name)
                        else:
                            logger.error("Failed to start download to " + cp_to_server + ":" + cp_to_file)

        self.swap_disk_thread_count = self.swap_disk_thread_count - 1

    def enable_replication(self):
        storage_servers = config['storage_server']
        self.en_rep_thread_count = 0
        for storage_server in storage_servers:
            self.en_rep_thread_count = self.en_rep_thread_count + 1
            thread.start_new_thread(self.poll_dirty_file, (storage_server,))

        while self.en_rep_thread_count > 0:
            pass

    def poll_dirty_file(self, storage_server):
        # TODO try this entire function and reduce thread count
        dirty_file = self._get_dirty_file(storage_server)
        if dirty_file == False:
            logger.error("Failed to get dirty file from storage server: " + storage_server)
            self.en_rep_thread_count = self.en_rep_thread_count - 1
            return False

        for file in set(dirty_file.split("\\n")):
            cp_from_detail = file.split("/")
            cp_from_server = storage_server
            try:
                cp_from_disk = cp_from_detail[1]
                cp_from_type = cp_from_detail[2]
                host_name = cp_from_detail[3]
            except IndexError:
                self.en_rep_thread_count = self.en_rep_thread_count - 1
                return True

            mapping = self._get_mapping("host", host_name)
            if cp_from_type == "primary":
                cp_to_type = "secondary"
            elif cp_from_type == "secondary":
                cp_to_type = "primary"

            try:
                cp_to_server = mapping[cp_to_type]["storage_server"]
                cp_to_disk = mapping[cp_to_type]["disk"]
                cp_to_file = file.replace(cp_from_disk,cp_to_disk).replace(cp_from_type, cp_to_type)
            except KeyError:
                self._remove_entry(cp_from_server, file, "dirty_files")
                self.en_rep_thread_count = self.en_rep_thread_count - 1
                return True

            torrent_url = self._create_torrent(cp_from_server, file)
            if torrent_url == False:
                logger.error("Failed to get torrent url for " + storage_server + ":" + file)
                self.en_rep_thread_count = self.en_rep_thread_count - 1
                return False
                
            if self._start_download(cp_to_server, cp_to_file, torrent_url) == True:
                self._remove_entry(cp_from_server, file, "dirty_files")
            else:
                logger.error("Failed to start download to " + cp_to_server + ":" + cp_to_file)

            self.en_rep_thread_count = self.en_rep_thread_count - 1

    def initialize_diskmapper(self, poll=False):
        if os.path.exists(self.mapping_file) and poll == False:
            os.remove(self.mapping_file)
        storage_servers = config['storage_server']
        self.ini_dm_thread_count = 0
        for storage_server in storage_servers:
            self.ini_dm_thread_count = self.ini_dm_thread_count + 1
            thread.start_new_thread(self.update_server_config, (storage_server,))

        while self.ini_dm_thread_count > 0:
            pass

    def update_server_config(self, storage_server):
        # TODO try this entire function and reduce thread count
        server_config = self._get_server_config(storage_server)
        if server_config == False:
            logger.error("Failed to get config from storage server: " + storage_server)
            self.ini_dm_thread_count = self.ini_dm_thread_count - 1
            return False

        bad_disks = self._get_bad_disks(storage_server)
        if server_config == False:
            logger.error("Failed to get bad disks form storage server: " + storage_server)
            self.ini_dm_thread_count = self.ini_dm_thread_count - 1
            return False

        for disk in server_config:
            if disk in bad_disks:
                status = "bad"
            else:
                status = "good"
            for type in server_config[disk]:
                host_name = server_config[disk][type]
                self._update_mapping(storage_server, disk, type, host_name, status)
                    
        self.ini_dm_thread_count = self.ini_dm_thread_count - 1

    def _create_torrent(self, storage_server, file):
        # http://netops-demo-mb-220.va2/api/membase_backup?action=create_torrent&file_path=/data_2/primary/empire-mb-user-b-001/zc1/incremental/test1/
        url = 'http://' + storage_server + '/api?action=create_torrent&file_path=' + file
        value = self._curl(url, 200)
        if value != False:
            return value
        return False

    def _add_entry(self, storage_server, entry, file_type):
        # http://netops-demo-mb-220.va2/api/membase_backup?action=add_entry&type=bad_disk&entry=%22/data_1%22
        url = 'http://' + storage_server + '/api?action=add_entry&entry=' + entry + '&type=' + file_type
        value = self._curl(url, 200)
        if value != False:
            return True
        return False

    def _remove_entry(self, storage_server, entry, file_type):
        # http://netops-demo-mb-220.va2/api/membase_backup?action=remove_entry&type=bad_disk&entry=%22/data_1%22
        url = 'http://' + storage_server + '/api?action=remove_entry&entry=' + entry.rstrip() + '&type=' + file_type
        value = self._curl(url, 200)
        if value != False:
            return True
        return False

    def _start_download(self, storage_server, file, torrent_url):
        # http://netops-demo-mb-220.va2/api/membase_backup?action=start_download&file_path=/data_3/secondary/empire-mb-user-b-001/zc1/&torrent_url=http://10.36.168.173/torrent/1347783417.torrent
        url = 'http://' + storage_server + '/api?action=start_download&file_path=' + file.rstrip() + '&torrent_url=' + torrent_url
        value = self._curl(url, 200)
        if value != False:
            return True
        return False

    def _get_bad_disks(self, storage_server):
        url = 'http://' + storage_server + '/api?action=get_file&type=bad_disk'
        value = self._curl(url, 200)
        if value != False:
            return json.loads(value)
        return False
        
    def _initialize_host(self, storage_server, host_name, type, game_id, disk, update_mapping=True):
        url = 'http://' + storage_server + '/api?action=initialize_host&host_name=' + host_name + '&type=' + type + '&game_id=' + game_id + '&disk=' + disk
        value = self._curl(url, 201)
        if value != False:
            if update_mapping == True:
                self._update_mapping(storage_server, disk, type, host_name)
            return True
        return False

    def _get_dirty_file(self, storage_server):
        url = 'http://' + storage_server + '/api?action=get_file&type=dirty_files'
        value = self._curl(url, 200)
        if value != False:
            return json.loads(value)
        return False

    def _get_server_config(self, storage_server):
        url = 'http://' + storage_server + '/api?action=get_config'
        value = self._curl(url, 200)
        if value != False:
            return json.loads(value)
        return False
        
    def _curl (self, url, exp_return_code=None):
        buf = cStringIO.StringIO()
        c = pycurl.Curl()
        c.setopt(c.URL, str(url))
        c.setopt(c.WRITEFUNCTION, buf.write)
        try:
            c.perform()
        except pycurl.error, error :
            errno, errstr = error
            if errno == 7:
                storage_server = url.split("/")[2]
                self._check_server_conn(storage_server)
            return False

        if c.getinfo(pycurl.HTTP_CODE) != exp_return_code and exp_return_code != None:
            return False

        value = buf.getvalue()
        buf.close()
        return value

    def _check_server_conn(self, storage_server):
        url = 'http://' + storage_server + '/api?action=get_config'
        c = pycurl.Curl()
        c.setopt(c.URL, str(url))
        for retry in range(3):
            try:
                time.sleep(5)
                c.perform()
            except pycurl.error, error:
                errno, errstr = erro
                if errno == 7:
                    logger.error("Failed to connect to " + storage_server)
            else:
              break
        else:
            self.swap_bad_disk(storage_server)

    def _is_diskmapper_initialized(self):
        if not os.path.exists(self.mapping_file):
            return False
        return True

    def _is_host_initialized(self, host_name):
        if not self._get_mapping ("host", host_name):
            return False
        return True
        

    def _is_bad_disk(self, type):
        try:
            if type["status"] == "bad":
                return True
        except KeyError:
            return False

    def _get_spare(self, type=None, skip=None):
        mapping = self._get_mapping("storage_server")
        if mapping == False:
            return False
    
        spare_mapping = {}
        spare_mapping["primary"] = []
        spare_mapping["secondary"] = []
        for storage_server in mapping:
            if storage_server == skip:
                continue
            for disk in mapping[storage_server]:
                for disk_type in mapping[storage_server][disk]:
                    if disk_type == "primary" or disk_type == "secondary":
                        host_name = mapping[storage_server][disk][disk_type]
                        if host_name == "spare" and mapping[storage_server][disk]["status"] != "bad":
                            if type == disk_type:
                                return { "disk" : disk, "storage_server" : storage_server}
                            spare_mapping[disk_type].append({ "disk" : disk, "storage_server" : storage_server})

        if type != None:
            return False
        return spare_mapping

    def _get_mapping(self, type, key = None):

        if not self._is_diskmapper_initialized(): 
            return False

        f = open(self.mapping_file, 'r')
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        file_content = pickle.load(f)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()
        
        if type == "host":
            mapping = {}
            for storage_server in file_content:
                for disk in file_content[storage_server]:
                    for disk_type in file_content[storage_server][disk]:
                        if disk_type == "primary" or disk_type == "secondary":
                            host_name = file_content[storage_server][disk][disk_type]
                            status = file_content[storage_server][disk]["status"]
                            if host_name != "spare" and status != "bad":
                                if host_name not in mapping.keys():
                                    mapping[host_name] = {}
                                mapping[host_name].update({disk_type : { "disk" : disk, "status" : status, "storage_server" : storage_server}})

        elif type == "storage_server":
            mapping = file_content

        if key == None:
            return mapping

        try:
            return mapping[key]
        except KeyError:
            return False
    
    def _start_response(self):
        self.start_response(self.status, self.response_headers)

    def _update_mapping(self, storage_server, disk, disk_type, host_name, status="good"):
        if os.path.exists(self.mapping_file):
            f = open(self.mapping_file, 'r+')
        else:
            f = open(self.mapping_file, 'w+')
            
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        file_content = f.read()
        if file_content != "":
            f.seek(0, 0)
            file_content = pickle.load(f)
            #file_content[storage_server][disk][disk_type] = host_name
        else:
            file_content = {}

        if storage_server in file_content.keys():
            if disk in file_content[storage_server].keys():
                #if disk_type in file_content[storage_server][disk].keys()
                file_content[storage_server][disk][disk_type] = host_name
                file_content[storage_server][disk]["status"] = status

            else:
                file_content[storage_server].update({disk : {disk_type : host_name, "status" : status}})
        else:
            file_content.update({storage_server : {disk : {disk_type : host_name, "status" : status}}})
        f.seek(0, 0)
        f.truncate()
        pickle.dump(file_content,f)
        f.seek(0, 0)
        verify_content = pickle.load(f)
        if verify_content != file_content:
            logger.error("Failed to update mapping for " + storage_server + " " + disk + " " + disk_type + " " + host_name + " " + status)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        os.chown(self.mapping_file, 48, -1)
        f.close()
        return True

