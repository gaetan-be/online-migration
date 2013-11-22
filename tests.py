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
import shutil

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
    
    CONFIG_PATH = "tests/test.ini"
    TEST_DB_NAME= "online_migration_test"
    
    def connection(self):
        
        config = ConfigParser.ConfigParser()
        config.read(TestOnlineMigration.CONFIG_PATH)
        return "%s:%s@%s:%s" % (config.get('MySQLServer','user'), config.get('MySQLServer','password'), config.get('MySQLServer','server'), config.get('MySQLServer','port'))
    
    def setUp(self):
        with capture() as nowhere:
            logging.info("Setup")
                
            logging.info("CREATE TABLE `%s`.`test_table` (  `id` int(11) unsigned NOT NULL AUTO_INCREMENT,  `name` varchar(50) DEFAULT NULL,  PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8" 
                                   % TestOnlineMigration.TEST_DB_NAME)
            shutil.rmtree(TestOnlineMigration.TEST_DB_NAME)
            try:
               migration = online_migration.OnlineMigration(server.get_server(u'online-migration', self.connection(), False))
               migration.server.exec_query("DROP DATABASE IF EXISTS `%s`;" % TestOnlineMigration.TEST_DB_NAME);
               migration.server.exec_query("CREATE DATABASE `%s`;" % TestOnlineMigration.TEST_DB_NAME);
               migration.server.exec_query("DELETE FROM online_migration.`migration_sys` WHERE `db`='%s';" % TestOnlineMigration.TEST_DB_NAME);

               migration.server.exec_query("DELETE FROM online_migration.`migration_sys` WHERE `db`='%s';" % TestOnlineMigration.TEST_DB_NAME);
               migration.server.exec_query("CREATE TABLE `%s`.`test_table` (`id` int(11) unsigned NOT NULL AUTO_INCREMENT,`name` varchar(50) DEFAULT NULL,PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8;" 
                                   % TestOnlineMigration.TEST_DB_NAME);
            
            except Exception, e:
                logging.error(u"ERROR: %s !" % e)
                sys.exit(1)
            
        
    def tearDown(self):
        with capture() as nowhere:
            logging.info("tearDown")
            try:
                migration = online_migration.OnlineMigration(server.get_server(u'online-migration', self.connection(), False))
                migration.server.exec_query("DROP DATABASE IF EXISTS `%s`;" % TestOnlineMigration.TEST_DB_NAME);
            except Exception, e:
                logging.error(u"ERROR: %s !" % e)
            
    def testInit(self):
        logging.info("testInit")
        try:
            online_migration.main(["-i", TestOnlineMigration.CONFIG_PATH,"init", TestOnlineMigration.TEST_DB_NAME])
        except Exception, e:
            logging.error(u"ERROR: %s !" % e)


if __name__ == "__main__":
    unittest.main()   

        