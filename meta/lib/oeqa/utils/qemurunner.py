# Copyright (C) 2013 Intel Corporation
#
# Released under the MIT license (see COPYING.MIT)

# This module provides a class for starting qemu images using runqemu.
# It's used by testimage.bbclass.

import subprocess
import os
import time
import signal
import re
import socket
import select
import errno

import logging
logger = logging.getLogger("BitBake.QemuRunner")

class QemuRunner:

    def __init__(self, machine, rootfs, display, tmpdir, deploy_dir_image, logfile, boottime):

        # Popen object for runqemu
        self.runqemu = None
        # pid of the qemu process that runqemu will start
        self.qemupid = None
        # target ip - from the command line
        self.ip = None
        # host ip - where qemu is running
        self.server_ip = None

        self.machine = machine
        self.rootfs = rootfs
        self.display = display
        self.tmpdir = tmpdir
        self.deploy_dir_image = deploy_dir_image
        self.logfile = logfile
        self.boottime = boottime
        self.logged = False

        self.runqemutime = 60

    def create_socket(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(0)
            sock.bind(("127.0.0.1",0))
            sock.listen(2)
            port = sock.getsockname()[1]
            logger.info("Created listening socket for qemu serial console on: 127.0.0.1:%s" % port)
            return (sock, port)

        except socket.error:
            sock.close()
            raise

    def log(self, msg):
        if self.logfile:
            with open(self.logfile, "a") as f:
                f.write("%s" % msg)

    def start(self, qemuparams = None):
        if self.display:
            os.environ["DISPLAY"] = self.display
        else:
            logger.error("To start qemu I need a X desktop, please set DISPLAY correctly (e.g. DISPLAY=:1)")
            return False
        if not os.path.exists(self.rootfs):
            logger.error("Invalid rootfs %s" % self.rootfs)
            return False
        if not os.path.exists(self.tmpdir):
            logger.error("Invalid TMPDIR path %s" % self.tmpdir)
            return False
        else:
            os.environ["OE_TMPDIR"] = self.tmpdir
        if not os.path.exists(self.deploy_dir_image):
            logger.error("Invalid DEPLOY_DIR_IMAGE path %s" % self.deploy_dir_image)
            return False
        else:
            os.environ["DEPLOY_DIR_IMAGE"] = self.deploy_dir_image

        try:
            self.server_socket, self.serverport = self.create_socket()
        except socket.error, msg:
            logger.error("Failed to create listening socket: %s" % msg[1])
            return False

        # Set this flag so that Qemu doesn't do any grabs as SDL grabs interact
        # badly with screensavers.
        os.environ["QEMU_DONT_GRAB"] = "1"
        self.qemuparams = 'bootparams="console=tty1 console=ttyS0,115200n8" qemuparams="-serial tcp:127.0.0.1:%s"' % self.serverport
        if qemuparams:
            self.qemuparams = self.qemuparams[:-1] + " " + qemuparams + " " + '\"'

        def getOutput(o):
            import fcntl
            fl = fcntl.fcntl(o, fcntl.F_GETFL)
            fcntl.fcntl(o, fcntl.F_SETFL, fl | os.O_NONBLOCK)
            return os.read(o.fileno(), 1000000)

        launch_cmd = 'runqemu %s %s %s' % (self.machine, self.rootfs, self.qemuparams)
        # FIXME: We pass in stdin=subprocess.PIPE here to work around stty
        # blocking at the end of the runqemu script when using this within
        # oe-selftest (this makes stty error out immediately). There ought
        # to be a proper fix but this will suffice for now.
        self.runqemu = subprocess.Popen(launch_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, preexec_fn=os.setpgrp)
        output = self.runqemu.stdout

        logger.info("runqemu started, pid is %s" % self.runqemu.pid)
        logger.info("waiting at most %s seconds for qemu pid" % self.runqemutime)
        endtime = time.time() + self.runqemutime
        while not self.is_alive() and time.time() < endtime:
            if self.runqemu.poll():
                if self.runqemu.returncode:
                    # No point waiting any longer
                    logger.info('runqemu exited with code %d' % self.runqemu.returncode)
                    self.stop()
                    logger.info("Output from runqemu:\n%s" % getOutput(output))
                    return False
            time.sleep(1)

        if self.is_alive():
            logger.info("qemu started - qemu procces pid is %s" % self.qemupid)
            cmdline = ''
            with open('/proc/%s/cmdline' % self.qemupid) as p:
                cmdline = p.read()
            try:
                ips = re.findall("((?:[0-9]{1,3}\.){3}[0-9]{1,3})", cmdline.split("ip=")[1])
                if not ips or len(ips) != 3:
                    raise ValueError
                else:
                    self.ip = ips[0]
                    self.server_ip = ips[1]
            except IndexError, ValueError:
                logger.info("Couldn't get ip from qemu process arguments! Here is the qemu command line used:\n%s\nand output from runqemu:\n%s" % (cmdline, getOutput(output)))
                self.stop()
                return False
            logger.info("Target IP: %s" % self.ip)
            logger.info("Server IP: %s" % self.server_ip)
            logger.info("Waiting at most %d seconds for login banner" % self.boottime)
            endtime = time.time() + self.boottime
            socklist = [self.server_socket]
            reachedlogin = False
            stopread = False
            qemusock = None
            bootlog = ''
            while time.time() < endtime and not stopread:
                sread, swrite, serror = select.select(socklist, [], [], 5)
                for sock in sread:
                    if sock is self.server_socket:
                        qemusock, addr = self.server_socket.accept()
                        qemusock.setblocking(0)
                        socklist.append(qemusock)
                        socklist.remove(self.server_socket)
                        logger.info("Connection from %s:%s" % addr)
                    else:
                        data = sock.recv(1024)
                        if data:
                            self.log(data)
                            bootlog += data
                            if re.search(".* login:", bootlog):
                                self.server_socket = qemusock
                                stopread = True
                                reachedlogin = True
                                logger.info("Reached login banner")
                        else:
                            socklist.remove(sock)
                            sock.close()
                            stopread = True

            if not reachedlogin:
                logger.info("Target didn't reached login boot in %d seconds" % self.boottime)
                lines = "\n".join(bootlog.splitlines()[-25:])
                logger.info("Last 25 lines of text:\n%s" % lines)
                logger.info("Check full boot log: %s" % self.logfile)
                self.stop()
                return False

            (status, output) = self.run_serial("root\n", raw=True)
            if re.search("root@[a-zA-Z0-9\-]+:~#", output):
                self.logged = True
                logger.info("Logged as root in serial console")
            else:
                logger.info("Couldn't login into serial console"
                        " as root using blank password")

        else:
            logger.info("Qemu pid didn't appeared in %s seconds" % self.runqemutime)
            self.stop()
            logger.info("Output from runqemu:\n%s" % getOutput(output))
            return False

        return self.is_alive()

    def stop(self):

        if self.runqemu:
            logger.info("Sending SIGTERM to runqemu")
            try:
                os.killpg(self.runqemu.pid, signal.SIGTERM)
            except OSError as e:
                if e.errno != errno.ESRCH:
                    raise
            endtime = time.time() + self.runqemutime
            while self.runqemu.poll() is None and time.time() < endtime:
                time.sleep(1)
            if self.runqemu.poll() is None:
                logger.info("Sending SIGKILL to runqemu")
                os.killpg(self.runqemu.pid, signal.SIGKILL)
            self.runqemu = None
        if hasattr(self, 'server_socket') and self.server_socket:
            self.server_socket.close()
            self.server_socket = None
        self.qemupid = None
        self.ip = None

    def restart(self, qemuparams = None):
        logger.info("Restarting qemu process")
        if self.runqemu.poll() is None:
            self.stop()
        if self.start(qemuparams):
            return True
        return False

    def is_alive(self):
        qemu_child = self.find_child(str(self.runqemu.pid))
        if qemu_child:
            self.qemupid = qemu_child[0]
            if os.path.exists("/proc/" + str(self.qemupid)):
                return True
        return False

    def find_child(self,parent_pid):
        #
        # Walk the process tree from the process specified looking for a qemu-system. Return its [pid'cmd]
        #
        ps = subprocess.Popen(['ps', 'axww', '-o', 'pid,ppid,command'], stdout=subprocess.PIPE).communicate()[0]
        processes = ps.split('\n')
        nfields = len(processes[0].split()) - 1
        pids = {}
        commands = {}
        for row in processes[1:]:
            data = row.split(None, nfields)
            if len(data) != 3:
                continue
            if data[1] not in pids:
                pids[data[1]] = []

            pids[data[1]].append(data[0])
            commands[data[0]] = data[2]

        if parent_pid not in pids:
            return []

        parents = []
        newparents = pids[parent_pid]
        while newparents:
            next = []
            for p in newparents:
                if p in pids:
                    for n in pids[p]:
                        if n not in parents and n not in next:
                            next.append(n)
                if p not in parents:
                    parents.append(p)
                    newparents = next
        #print "Children matching %s:" % str(parents)
        for p in parents:
            # Need to be careful here since runqemu-internal runs "ldd qemu-system-xxxx"
            # Also, old versions of ldd (2.11) run "LD_XXXX qemu-system-xxxx"
            basecmd = commands[p].split()[0]
            basecmd = os.path.basename(basecmd)
            if "qemu-system" in basecmd and "-serial tcp" in commands[p]:
                return [int(p),commands[p]]

    def run_serial(self, command, raw=False):
        # We assume target system have echo to get command status
        if not raw:
            command = "%s; echo $?\n" % command
        self.server_socket.sendall(command)
        data = ''
        status = 0
        stopread = False
        endtime = time.time()+5
        while time.time()<endtime and not stopread:
            sread, _, _ = select.select([self.server_socket],[],[],5)
            for sock in sread:
                answer = sock.recv(1024)
                if answer:
                    data += answer
                    # Search the prompt to stop
                    if re.search("[a-zA-Z0-9]+@[a-zA-Z0-9\-]+:~#", data):
                        stopread = True
                        break
                else:
                    sock.close()
                    stopread = True
        if data:
            if raw:
                status = 1
            else:
                # Remove first line (command line) and last line (prompt)
                data = data[data.find('$?\r\n')+4:data.rfind('\r\n')]
                index = data.rfind('\r\n')
                if index == -1:
                    status_cmd = data
                    data = ""
                else:
                    status_cmd = data[index+2:]
                    data = data[:index]
                if (status_cmd == "0"):
                    status = 1
        return (status, str(data))
