#!/usr/bin/python

import sys
import os
import hashlib
import re
import glob
import pickle
import StringIO
import logging
import ConfigParser
import argparse

online_migration = __import__('online-migration')

import unittest
from mysql.utilities.common import (database, options, server, table)
from mysql.utilities.command import dbcompare, dbcopy, dbexport
from mysql.utilities.common.ip_parser import parse_connection

from contextlib import contextmanager


@contextmanager
def capture():
    old_stdout = sys.stdout
    sys.stdout = StringIO.StringIO()
    try:
        yield sys.stdout
    finally:
        sys.stdout = old_stdout

class TestOnlineMigration(unittest.TestCase):
    
    def connection(self):
        config = ConfigParser.ConfigParser()
        config.read("tests/test.ini")
        return "%s:%s@%s:%s" % (config.get('MySQLServer','user'), config.get('MySQLServer','password'), config.get('MySQLServer','server'), config.get('MySQLServer','port'))
    
    def setUp(self):
        with capture() as nowhere:
            logging.info("Setup")
            
            try:
               migration = online_migration.OnlineMigration(server.get_server(u'online-migration', self.connection(), False))
               migration.server.exec_query("DROP DATABASE IF EXISTS `online_migration_test`;");
               migration.server.exec_query("CREATE DATABASE `online_migration_test`;");
               migration.server.exec_query("DELETE FROM online_migration.`migration_sys` WHERE `db`='online_migration_test';");

            except Exception, e:
                logging.error(u"ERROR: %s !" % e)
                sys.exit(1)
            
        
    def tearDown(self):
        with capture() as nowhere:
            logging.info("tearDown")
            try:
                migration = online_migration.OnlineMigration(server.get_server(u'online-migration', self.connection(), False))
                migration.server.exec_query("DROP DATABASE IF EXISTS `online_migration_test`;");
            except Exception, e:
                logging.error(u"ERROR: %s !" % e)
            
    def testInit(self):
        logging.info("testInit")
        self.assertEqual(1, 1)
        #try:
        # online_migration.main(["init","imdb"])
        #except:
        #print "An error occured"


if __name__ == "__main__":
    unittest.main()   

        