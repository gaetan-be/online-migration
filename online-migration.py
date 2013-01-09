#!/usr/bin/python

import sys
import os
import inspect
import hashlib
import re
import glob
import StringIO

#sys.path.append("./mysql")
from mysql.utilities.common.database import Database
from mysql.utilities.common.table import Table
from mysql.utilities.common.server import get_server
from mysql.utilities.common.options import parse_connection
from mysql.utilities.command import dbcopy
from mysql.utilities.command.dbcompare import database_compare
from subprocess import call
from contextlib import contextmanager

#from dbcompare import get_common_objects_mig
#from dbcompare import diff_objects_mig, get_object_bef_mig
database = "online_migration"
table = "migration_sys"
dbtable = database + "." + table
tmp_prefix="tmp_online_mig"

# function to connect to MySQL
def connect_db(server,database):
   options={}
   options["skip_grants"] = True
   db_obj = Database(server, database, options)
   return db_obj

# function that creates the needed system database and table used by
# online-migration
def call_init_sysdb():
   db_obj = connect_db(server,database)
   if not db_obj.exists():
      print "\nThe database does not exist: {0}".format(database)
      print "Creating the database: {0}".format(database)
      db_obj.create(server,database)
   tbl_obj = Table(server,dbtable)  
   if not tbl_obj.exists():
      print "\nThe table does not exist: {0}".format(table)
      print "Creating the table: {0}".format(table)
      try:
          res = server.exec_query("CREATE TABLE " + dbtable + 
                                   "(id int auto_increment primary key," +
                                   "db varchar(100), version int," +
                                   "start_date datetime, " +
                                   "apply_date datetime, status varchar(10));")
      except Exception, e:
          print "ERROR: problem creating the system table %s !" % e 
          sys.exit(1)
   else:
        print "\nWARNING: system table already exists"
      

# check that a db was entered
def check_arg(num=1):
   if len(sys.argv) <= num+1:
       print "\nERROR: %i arguments are required with command %s !" % (num, sys.argv[1])
       sys.exit(1) 
# check if the system table are created     
def check_sys_init():
   db_obj = connect_db(server,database)
   error = 0
   if not db_obj.exists():
       error = 1
   else:
      tbl_obj = Table(server,dbtable)  
      if not tbl_obj.exists():
        error = 1
   if error == 1:
      print "\nERROR: online-migration was not initialized on this server!"
      print "       please run online-migration init_sysdb."
      sys.exit(1)
   return 0 

def calculate_md5(filename):
    md5=hashlib.md5()
    with open(filename,'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            md5.update(chunk)
    return md5.hexdigest()

def create_meta(db_name,version,md5check,comment):
    file_meta = open("%s/%04d-up.meta" % (db_name, int(version)), 'w')
    file_meta.write("%s\n" % version)
    file_meta.write("%s\n" % md5check)
    file_meta.write("%s\n" % comment)
    file_meta.close()
    
def check_init(db_name):
    # check if there is already a migration for that database
    query = "SELECT * FROM %s WHERE `db` = \"%s\";" % (dbtable, db_name)
    res = server.exec_query(query)
    return (res is not None and len(res)>= 1)
         
def call_online_schema_change(db_name, version, file_name, cmd='up'):
    f = open(file_name,"r")
    for line in iter(f): 
       call_change_migration_status(db_name,version,'running')
       line_list=line.split("::")
       if line_list[0] == "OM_IGNORE_TABLE":
           query = line_list[1] 
           query_options = {
            'params': (db_name,) 
           }
           try: 
               res = server.exec_query("use %s" % db_name)
               res = server.exec_query(query)
           except Exception, e:
               print "ERROR: %s !" % e 
               sys.exit(1)
           
           # create the undo for table creation here
           if cmd == "up":
               file_down = open("%s/%04d-down.mig" % (db_name, int(version)), 'a')
               if re.search('^create',query, re.IGNORECASE):
                   query = re.sub("`"," ",query,0,re.IGNORECASE)
                   regex = re.compile("create\s+table\s+(\w+)",re.IGNORECASE)
                   r = regex.search(query)
                   table=r.group(1)
                   file_down.write("DROP TABLE %s;\n" % table)
               file_down.close()
           
       else:
           cmd="./pt-online-schema-change h=localhost,u=root,D=\"%s\",t=%s --alter=\"%s\" --execute >>online_migration.log 2>&1" % (db_name, line_list[0], line_list[1])
           #print cmd
           if call(cmd, shell=True) != 0:
               print "ERROR: problem while running :\n   %s" % cmd
               sys.exit(1)
    call_change_migration_status(db_name,version,'ok')
        
def call_write_stmt_up(table,alter_stmt,file):
    alter_stmt = alter_stmt.replace('"','\"')
    alter_stmt = re.sub("alter\s+table\s+%s" % table,"",alter_stmt,1,re.IGNORECASE)
    alter_stmt = alter_stmt.replace('\n',' ')
    file.write("%s::%s\n" % (table, alter_stmt))

    
def call_change_migration_status(db_name, version, status):
    last_version=call_last_migration_version(db_name)
    if last_version is None:
        last_version="-1"
    if int(last_version) == int(version):
        query = "UPDATE %s SET STATUS = '%s', apply_date=now() WHERE db = '%s' AND version = %s" % (dbtable, status, db_name, version) 
    else:
        query = "SELECT version FROM %s WHERE `version` = %s and `db` = \"%s\";" % (dbtable, version, db_name)
        res = server.exec_query(query)
        if (res is None or len(res) < 1):
            query = "INSERT INTO %s VALUES (0,'%s',%s,now(),now(),'ok')" % (dbtable, db_name, version) 
        else:
            query = "UPDATE %s SET STATUS = '%s', apply_date=now() WHERE db = '%s' AND version = %s" % (dbtable, status, db_name, version) 
    try: 
        res = server.exec_query(query)
    except Exception, e:
        print "ERROR: %s !" % e 
        sys.exit(1)
        
def call_pending_migration(db_name, last_ver):
    pend=0
    metafiles = glob.glob('%s/*.meta' % db_name)
    metafiles.sort()
    for mig in metafiles:
        a=mig.split('/')
        b=a[1].split('.') 
        c=b[0].split('-')
        if int(c[0]) > int(last_ver):
            pend=pend+1
    return pend
                
def call_new_migration_version(db_name):
    last_version=call_last_migration_version(db_name)
    return int(last_version)+1

def call_last_migration_version(db_name):
    query = "SELECT max(version) FROM %s WHERE status <> 'rollback' AND `db` = \"%s\";" % (dbtable, db_name)
    res = server.exec_query(query)
    if res is None:
        print "ERROR: there is no migration initilized for database %s !" % db_name
        sys.exit(1)
    return (res[0][0])
   
def call_add_up_in_db(db_name,version):
    query = "INSERT INTO %s VALUES (0,'%s',%s,now(),now(),'started');" % (dbtable, db_name, version) 
    try: 
        res = server.exec_query(query)
    except Exception, e:
        print "ERROR: %s !" % e 
        sys.exit(1)
def call_create_migration_file(db_name, file_name, version, direction):
    file_up = open("%s/%04d-%s.mig" % (db_name, int(version), direction), 'w')
    f = open(file_name,"r")
    alter_stmt=""
    other_stmt=""
    open_stmt=0  # 1=alter, 2=other (insert, create, drop)
    for line in iter(f):
       if re.search('^alter',line, re.IGNORECASE):
           open_stmt=1
           if len(alter_stmt) > 0:
              #save the alter statement in a migration file
              call_write_stmt_up(table,alter_stmt,file_up)
           if len(other_stmt) > 0:
              call_write_stmt_up("OM_IGNORE_TABLE", other_stmt, file_up)
              other_stmt=""
           alter_stmt=line
           #regex = re.compile("alter\s+table\s+(\w+)",re.IGNORECASE)
           regex = re.compile("alter\s+table\s+(.*)\s+",re.IGNORECASE)
           r = regex.search(line)
           table=r.group(1)
           
       else:
           if re.search('^insert|^create|^drop',line, re.IGNORECASE) and not re.search(' column | primary | key ',line, re.IGNORECASE):
               open_stmt=2
               if len(other_stmt) > 0:
                  call_write_stmt_up("OM_IGNORE_TABLE", other_stmt, file_up)
                  other_stmt=""
               if len(alter_stmt) > 0:
                  call_write_stmt_up(table,alter_stmt,file_up)
                  alter_stmt=""
               other_stmt+=line
           else:
               if open_stmt == 1:
                   alter_stmt+=line
               else:
                   other_stmt+=line 
    #save the alter statement in a migration file
    if open_stmt == 1:
        call_write_stmt_up(table,alter_stmt,file_up)
    else:
        call_write_stmt_up("OM_IGNORE_TABLE", other_stmt, file_up)
    file_up.close() 
           
def call_create_migration(db_name,file_name,comment=""): 
    if not os.path.exists(file_name):        
        print "\nERROR: %s doesn't exist !" % file_name
        sys.exit(1)
    db_obj = connect_db(server,db_name)
    if not db_obj.exists():
        print "\nERROR: database %s doesn't exist !" % db_name
        sys.exit(1)
    # find the migration version
    last_version=call_last_migration_version(db_name)
    # check first if there are pending migrations
    pend=call_pending_migration(db_name, last_version)        
    if pend > 0:
        print "ERROR: you have %s pending migration(s) !" % pend
        sys.exit(1)
    version=call_new_migration_version(db_name)
    call_create_migration_file(db_name, file_name, version, "up")
    call_add_up_in_db(db_name,version)
    call_online_schema_change(db_name,version,"%s/%04d-up.mig" % (db_name, int(version)))
    print "\nmigration %04d created successfully !" % int(version)
    md5check=call_create_checksum(db_name,version)    
    create_meta(db_name,version,md5check,comment)
    
def call_create_checksum(db_name, version):
    file_desc_tmp = open("%s/%04d-up.tmp" % (db_name, int(version)), 'w')
    db_obj = connect_db(server, db_name)
    table_names = [obj[0] for obj in db_obj.get_db_objects('TABLE')]
    for tblname in table_names:
        query = "DESC %s.%s;" % (db_name, tblname)
        res = server.exec_query(query)
        file_desc_tmp.write("%s\n" % ' '.join(str(x) for x in res)) 
    file_desc_tmp.close() 
    md5check = calculate_md5("%s/%04d-up.tmp" % (db_name, int(version)))
    os.remove("%s/%04d-up.tmp" % (db_name, int(version))) 
    return md5check
        
# function to initiate the first migration      
def call_init_migration(db_name): 
    if check_init(db_name):
        print "ERROR: init was already performed for database %s !" % db_name
        sys.exit(1)
    query = "INSERT INTO %s VALUES (0,'%s',0,now(),now(),'ok');" % (dbtable, db_name) 
    if os.path.exists("%s/0000-up.mig" % db_name):
       print "ERROR: there's already an init file for this schema (%s/0000-up.mig)" % db_name
       sys.exit(1)
         
                    
    try: 
        res = server.exec_query(query)
    except Exception, e:
        print "ERROR: %s !" % e 
        sys.exit(1)
        
    db_obj = connect_db(server, db_name)
    table_names = [obj[0] for obj in db_obj.get_db_objects('TABLE')]
    if not os.path.exists(db_name):
        os.makedirs(db_name)
    file_up = open(db_name + "/" + "0000-up.mig", 'w')
    file_up.write("CREATE DATABASE %s;\n" % db_name)
    file_up.write("USE %s;\n" % db_name)
    for tblname in table_names:
        file_up.write("#table: %s\n" % tblname)
        query = "SHOW CREATE TABLE %s.%s;" % (db_name, tblname)
        res = server.exec_query(query) 
        file_up.write("%s\n" % res[0][1]) 
    file_up.close()
    md5check=call_create_checksum(db_name, 0)    
    create_meta(db_name,0,md5check,"Initial file")

#display the status of the migration for all or one schema
def call_status(db_name=None):
    query = "SELECT distinct db FROM %s;" % dbtable
    res = server.exec_query(query)
    if res is None:
        print "Warning: no migration was ever initiate on this server !"
        #sys.exit(1)
    if db_name is None:
        for db in res:
            call_status_db(db[0])
            
    
    else:
        query = "SELECT distinct db FROM %s where db = '%s';" % (dbtable, db_name)
        res = server.exec_query(query)
        if (res is None or len(res) < 1):
            print "Warning: no migration was ever initiate on this server for %s  !" % db_name
            if not os.path.exists(db_name):
                print "ERROR: no data related to any migration available !"
                sys.exit(1)
        call_status_db(db_name)    
        
def call_status_db(db_name):
    query = "SELECT version, apply_date, status FROM %s where db = '%s';" % (dbtable, db_name)
    res = server.exec_query(query)
    last_ver=-1
    print "\nMigration of schema %s : " % db_name
    print '  +---------+---------------------+------------------+------------------------+'
    print '  | VERSION | APPLIED             | STATUS           |                COMMENT |'
    print '  +---------+---------------------+------------------+------------------------+'
    if len(res) > 0:
        # before displaying the status, let's verify the checksum
        last_ver=call_last_migration_version(db_name)
        for records in res: 
            # TODO: read meta data for each version to add the coment
            (ver,md5,comment)=read_meta(db_name, records[0])
            status=records[2]
            if ver == last_ver:
                if not verify_checksum(db_name,ver,md5) and status != "rollback":
                    status="checksum problem"
            ver="%04d" % int(ver)    
            print "  | %7s | %s | %16s | %22s |" % (ver, records[1], status, comment[:22])
    metafiles = glob.glob('%s/*.meta' % db_name)
    metafiles.sort()
    for mig in metafiles:
        a=mig.split('/')
        b=a[1].split('.') 
        c=b[0].split('-')
        if int(c[0]) > int(last_ver):
            (ver,md5,comment)=read_meta(db_name, int(c[0]))
            print "  | %7s | %19s | %16s | %22s |" % (c[0], 'none', 'pending', comment[:22])
                 
    print '  +---------+---------------------+------------------+------------------------+'

def verify_checksum(db_name,version,md5):
    checksum=call_create_checksum(db_name, version)
    #print "DEBUG md5=%s     checksum=%s" % (md5,checksum)
    if checksum == md5:
        return True
    else: 
        return False
    

def read_meta(db_name, version):
    f=open("%s/%04d-up.meta" % (db_name, int(version)), 'r')
    ver=f.readline()
    md5=f.readline()
    comment=f.readline()
    return(ver.rstrip(),md5.rstrip(),comment.rstrip()) 

def call_mysql_create_schema(db_name, file_name):
    f = open(file_name,"r")
    call_change_migration_status(db_name,0,'running')
    cmd="mysql -u root < %s >>online_migration.log 2>&1" % file_name
    if call(cmd, shell=True) == 0:
        print "Schema creation run successfully"
    else:
        print "ERROR: problem while running :\n   %s" % cmd
        sys.exit(1)
    call_change_migration_status(db_name, 0, 'ok')
    
@contextmanager
def capture():
    old_stdout = sys.stdout
    sys.stdout = StringIO.StringIO()
    try:
        yield sys.stdout
    finally:
        sys.stdout = old_stdout    

# Main program
if len(sys.argv) < 2:
    print "ERROR: a command is needed"
    sys.exit(1)
else:
    with capture() as nowhere:
        server=get_server("localhost","root@localhost:3306", False)
    if sys.argv[1]  == 'init_sysdb':
        call_init_sysdb()
    elif sys.argv[1] == 'init':
        check_arg()
        check_sys_init()
        call_init_migration(sys.argv[2])
    elif sys.argv[1] == 'create':
        check_arg(2)
        check_sys_init()
        if len(sys.argv) > 4:
            comment=sys.argv[4]
        else:
            comment="none"
        call_create_migration(sys.argv[2],sys.argv[3],comment)
    elif sys.argv[1] == 'status':
        check_arg(0)
        check_sys_init()
        if len(sys.argv) == 3:
            db_name=(sys.argv[2])
        else:
            db_name=None
        call_status(db_name)
    elif sys.argv[1] == 'checksum':
        check_arg(1)
        check_sys_init()
        db_name=(sys.argv[2])
        checksum=call_create_checksum(db_name, "0")
        print "%s's current schema cheksum = %s" % (db_name, checksum) 
    elif sys.argv[1] == 'down':
        check_arg(1)
        check_sys_init()
        db_name=(sys.argv[2])
        last_version=call_last_migration_version(db_name)
        if last_version is not None and int(last_version) > 0:
            print "rollback from %04d to %04d" % (int(last_version), int(last_version)-1)
            call_online_schema_change(db_name,last_version,"%s/%04d-down.mig" % (db_name, int(last_version)),'down')
            call_change_migration_status(db_name,last_version,'rollback')
        else:
            print "ERROR: impossible to rollback as nothing was migrated yet !"
            sys.exit(1)
    elif sys.argv[1] == 'up':
        check_arg(1)
        check_sys_init()
        db_name=(sys.argv[2])
        last_version=call_last_migration_version(db_name)
        if last_version is not None:
            (ver,md5,comment)=read_meta(db_name, int(last_version))
            if verify_checksum(db_name,last_version,md5) is False:
                print "Warning: the current schema doesn't match the last applied migration"
            version=call_new_migration_version(db_name)
            if not os.path.exists("%s/%04d-up.mig" % (db_name, int(version))):        
                print "\nNo migration available"
            else:
                print "Preparing migration to version %04d" % int(version)
                if os.path.exists("%s/%04d-down.mig" % (db_name, int(version))):        
                    os.remove("%s/%04d-down.mig" % (db_name, int(version))) 
                    
                (ver,md5,comment)=read_meta(db_name, int(version))
                
                options={'skip_data': True, 'force': True}
                db_list = []
                grp = re.match("(\w+)(?:\:(\w+))?","%s:%s_%s" % (db_name, tmp_prefix, db_name) )
                db_entry = grp.groups()
                db_list.append(db_entry)
                source_values = parse_connection("root@localhost")
                destination_values = parse_connection("root@localhost")
                with capture() as stepback:
                    dbcopy.copy_db(source_values, destination_values, db_list, options)
                call_online_schema_change(db_name,version,"%s/%04d-up.mig" % (db_name, int(version)))
                if verify_checksum(db_name,version,md5) is True:
                    print "Applied changes match the requested schema"
                else:
                    print "Something didn't run as expected, db schema doesn't match !"
                    call_change_migration_status(db_name,version,'invalid checksum')
                    
                options={'run_all_tests': True, 'reverse': True, 'verbosity': None, 
                         'no_object_check': False, 'no_data': True, 'quiet': True, 
                         'difftype': 'sql', 'width': 75, 'changes-for': 'server1', 'skip_grants': True}
                with capture() as stepback:
                    res = database_compare(source_values, destination_values, db_name , "%s_%s" % (tmp_prefix, db_name), options) 
                str=stepback.getvalue().splitlines(True)
                to_add=0
                file_down = open("%s/%04d-down.tmp" % (db_name, int(version)), 'a')
                for line in str:
                  if  line[0] not in ['#', '\n', '+', '-', '@' ]:
                      line = re.sub(" %s\." % db_name," ",line,1,re.IGNORECASE)
                      file_down.write("%s\n" % line.strip())
                  elif re.match("# WARNING: Objects in",line):
                      if re.match("# WARNING: Objects in \w+\.tmp_online_mig_", line):
                          to_add=2
                      else:
                          to_add=1
                  else: 
                      grp = re.match("#\s+TABLE\: (\w+)", line )
                      if grp: 
                          if to_add == 2:
                              query = "SHOW CREATE TABLE tmp_online_mig_%s.%s;" % (db_name, grp.group(1))
                              res = server.exec_query(query) 
                              file_down.write("%s\n" % res[0][1]) 
                          elif to_add == 1:
                              file_down.write("DROP TABLE %s;\n" % grp.group(1))
                file_down.close()
                call_create_migration_file(db_name, "%s/%04d-down.tmp" % (db_name, int(version)), version, "down")
                query = "DROP DATABASE %s_%s" % (tmp_prefix, db_name)
                res = server.exec_query(query) 
                os.remove("%s/%04d-down.tmp" % (db_name, int(version))) 

        else:
            call_mysql_create_schema(db_name, "%s/0000-up.mig" % db_name)
    