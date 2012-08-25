#!/bin/env python
"""
This module maps request to function based on the url and method
"""
import os
import glob
import hashlib


class StorageServer:

    def __init__(self, environ, start_response):
        self.environ  = environ
        self.query_string  = environ["QUERY_STRING"]
        self.start_response = start_response
        self.status = '400 Bad Request'
        self.response_headers = [('Content-type', 'text/plain')]

    def list(self):
        self.status = '202 Accepted'
        path = self.environ["PATH_TRANSLATED"]

        if not os.path.exists(path):
            self.status = '400 Bad Request'
            files = "File not found."
        elif not os.access(path, os.R_OK):
            self.status = '403 Forbidden'
            files = "Cannot access file."
        else:
            file_list = self._file_iterater(path)
            files = self._get_s3_path(file_list)

        self._start_response()
        return [files + "\n"]

    def save_to_disk(self):
        self.status = '200 OK'
        path = self.environ["PATH_TRANSLATED"]
        path_info = self.environ["PATH_INFO"]

        if not self._is_host_initialized(path_info):
            self.status = '417 Expectation Failed'
            self._start_response()
            return "Host Not initialized for path : " + path_info
            
        block_size = 4096
        file_size = int(self.environ.get('CONTENT_LENGTH','0'))
        chunks = file_size / block_size
        last_chunk_size = file_size % block_size
    
        f = open(path,'wb')
        while chunks is not 0:
            file_chunk = self.environ['wsgi.input'].read(4096)
            f.write(file_chunk)
            chunks -= 1

        file_chunk = self.environ['wsgi.input'].read(last_chunk_size)
        f.write(file_chunk)
        f.close()

        self._start_response()
        return ["Saved file to disk"]

    def delete(self):
        self.status = '202 Accepted'
        path = self.environ["PATH_TRANSLATED"]

        if not os.path.exists(path):
            self.status = '404 Not Found'
            self._start_response()
            return ["File not found."]
            
        if not os.access(path, os.W_OK):
            self.status = '403 Forbidden'
            self._start_response()
            return ["No permission to delete file."]

        if os.path.isdir(path):
            for root, dirs, files in os.walk(path, topdown=False, followlinks=True):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
        else:
            os.remove(path)

        if os.path.exists(path):
            self.status = '400 Bad Request'
        else:
            self.status = '200 OK'
            
        self._start_response()
        return ["Deleted " + self.environ["SERVER_NAME"] + self.environ["PATH_INFO"]]

    def _is_host_initialized(self, path):
        subfolders = path.split('/')
        document_root = self.environ["DOCUMENT_ROOT"]
        host_folder = document_root + "/" + subfolders[1] + "/" + subfolders[2]
        if os.path.isdir(host_folder):
            return True
        return False

    def _file_iterater(self, path, recursive=False):

        files = []

        if not os.path.isdir(path):
            self.response_headers.append(('Etag', self._get_md5sum(open(path, "r"))))
            return path
            
        if "recursive=true" not in self.query_string:
            for item in os.listdir(path):
                full_path = os.path.join(path, item)
                if os.path.isdir(full_path):
                    full_path = full_path + "/"
                files.append(full_path)

        else:
            for root, dirnames, filenames in os.walk(path, followlinks=True):
                for name in filenames:
                    files.append(os.path.join(root, name))
                for name in dirnames:
                    files.append(os.path.join(root, name) + "/")

        return "\n".join(sorted(files))
    
    def _get_md5sum(self, file, block_size=2**20):
        md5 = hashlib.md5()
        while True:
            data = file.read(block_size)
            if not data:
                break
            md5.update(data)
        return md5.hexdigest()

    def _get_s3_path(self, files):

        return files.replace(self.environ["DOCUMENT_ROOT"], "s3://" +
                                         self.environ["SERVER_NAME"] )

    def _start_response(self):
        self.start_response(self.status, self.response_headers)

