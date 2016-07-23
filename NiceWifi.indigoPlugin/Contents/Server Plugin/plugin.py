
#! /usr/bin/env python
# -*- coding: utf-8 -*-
######################################################################################

import os
import sys
import select
import httplib
import urllib2
import indigo
import math
import decimal
import datetime
import socket
import json
from SocketServer import ThreadingMixIn
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import threading
from ghpu import GitHubPluginUpdater
import time
from pexpect import pxssh
import urlparse

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""

class httpHandler(BaseHTTPRequestHandler):
    def __init__(self, plugin,*args):
        try:
            self.plugin = plugin
            self.plugin.debugLog(u"HTTP Server: New httpHandler thread: "+threading.currentThread().getName()+", total threads: "+str(threading.activeCount()))
            BaseHTTPRequestHandler.__init__(self,*args)
        except Exception, e:
            self.plugin.errorLog(u"HTTP Server: Error: " + str(e))
 
    def do_GET(self):             
        self.receivedMessage()
      
    def do_POST(self):         
        self.receivedMessage()
            
    def receivedMessage(self):    
        try:
            self.send_response(200)
            self.end_headers()  
            self.wfile.write('{"result":"ok"}')

            ipaddress = self.client_address[0]
            parsed_url= urlparse.urlparse(self.path)
            verb      = self.command

            url       = parsed_url.path
            query     = parsed_url.query
            
            length    = int(self.headers['Content-Length'])   
            body      = self.rfile.read(length)
            
            self.plugin.debugLog(u"HTTP Server: Received HTTP " + self.command + " request from '" + ipaddress + "'") 

            #timestr = time.strftime("%Y%m%d-%H%M%S")
            #f = open('/Users/canteula/Airodump-' + timestr + '.json',mode='w')
            #f.write(body)
            #f.close()

            self.plugin.parseAirodumpMessage(body) 
            
           
        except Exception, e:
            self.plugin.errorLog(u"HTTP Server: Error: " + str(e))
        return

class Plugin(indigo.PluginBase):
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        self.updater = GitHubPluginUpdater(self)
        
        # Timeout
        self.reqTimeout = 8
        
        # create empty device list
        self.deviceList = {}
 
        self.httpServerPort = 0
        self.sock = None
               
        self.CreatingDevice = False

    def __del__(self):
        indigo.PluginBase.__del__(self)

    ###################################################################
    # Plugin
    ###################################################################

 
    def deviceStartComm(self, device):
        self.debugLog(device.name + ": Starting device") 
        self.addDeviceToList(device)

    def addDeviceToList(self, device):
        if device: 
            if device.deviceTypeId in ["airodump-bssid","airodump-station"]:
                lastTimeSensor = datetime.datetime.now() - datetime.timedelta(seconds=3600)          
                if device.id not in self.deviceList:
                    self.deviceList[device.id] = {
                    'ref':device, 
                    'address': device.pluginProps["address"],
                    'lastTimeSensor': lastTimeSensor
                    }
                

    def deleteDeviceFromList(self, device):
        if device:
            if device.id in self.deviceList:
                del self.deviceList[device.id]
    
    def deviceStopComm(self,device):
        if device.id not in self.deviceList:
            return
        self.debugLog(device.name + ": Stoping device")
        self.deleteDeviceFromList (device)   

    def startup(self):
        self.loadPluginPrefs()
        self.debugLog(u"startup called")       
        socket.setdefaulttimeout(self.reqTimeout)        
        self.startHTTPServer()
        self.updater.checkForUpdate()

    def shutdown(self):
        self.debugLog(u"shutdown called")

    def deviceCreated(self, device):
        indigo.server.log (u"Created new device \"%s\" of type \"%s\"" % (device.name, device.deviceTypeId))

    def deviceDeleted(self, device):
        indigo.server.log (u"Deleted device \"%s\" of type \"%s\"" % (device.name, device.deviceTypeId))
        self.deleteDeviceFromList (device)
             
    def loadPluginPrefs(self):
        # set debug option
        if 'debugEnabled' in self.pluginPrefs:
            self.debug = self.pluginPrefs.get('debugEnabled',False)
        else:
            self.debug = False

        self.httpServerPort   = int (self.pluginPrefs.get('httpserverport',8687))
                
        self.reqTimeout = 8
    
    def menuGetDevsBssid(self, filter, valuesDict, typeId, elemId):
        menuList = []
        for dev in indigo.devices.iter(filter="com.tenallero.indigoplugin.nicewifi.airodump-bssid"):
            if dev.enabled:
                menuList.append((dev.id, dev.name))
        return menuList
    
    ###################################################################
    # UI Validations
    ###################################################################

    def validateDeviceConfigUi(self, valuesDict, typeId, devId):        
        return (True, valuesDict)

    def validatePrefsConfigUi(self, valuesDict):
        self.debugLog(u"validating Prefs called")       
        port = int(valuesDict[u'httpserverport'])	
        if (port <= 0 or port>65535):
            errorMsgDict = indigo.Dict()
            errorMsgDict[u'port'] = u"Port number needs to be a valid TCP port (1-65535)."
            return (False, valuesDict, errorMsgDict)
        return (True, valuesDict)

    def closedPrefsConfigUi ( self, valuesDict, UserCancelled):
        #   If the user saves the preferences, reload the preferences
        if UserCancelled is False:
            indigo.server.log ("Preferences were updated, reloading Preferences...")            
            if not (self.httpServerPort == int(self.pluginPrefs.get('httpserverport',8687))):
                indigo.server.log("New listen port configured, reload plugin for change to take effect",isError=True)
            self.loadPluginPrefs()
            
    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, devId):
        if userCancelled is False:
            indigo.server.log ("Device settings were updated.")
            device = indigo.devices[devId]
            self.deleteDeviceFromList (device)
            self.addDeviceToList (device)

        
         
    def validateAddress (self,value):
        try:
            socket.inet_aton(value)
        except socket.error:
            return False
        return True

    ###################################################################
    # Device discovery
    ###################################################################

    def parseAirodumpMessage (self,body):
        #if self.CreatingDevice:
        #    return
        try:
            self.CreatingDevice = True
            data = json.loads (body)
            for i in range(len(data["detection-run"]["wireless-network"])):
                item = data["detection-run"]["wireless-network"][i]
                if item["type"] == "infrastructure":               
                    self.parseAirodumpInfrastructure(item)                
                elif item["type"] == "adhoc":
                    self.parseAirodumpAdhoc(item)          
                elif item["type"] == "probe":
                    self.parseAirodumpProbe(item)  
                else:
                    indigo.server.log ("Received unknown type: " + item["type"])             
                pass
        except:
            self.CreatingDevice = False
            return
        self.CreatingDevice = False
        return

    def parseAirodumpInfrastructure (self,item):
        found    = False
        device   = None
        deviceTypeId = "airodump-bssid"

        bssid    = item["BSSID"]
        address  = bssid

        if item["SSID"]["essid"]["cloaked"]:
            essid = "(hidden)"
            name  = "BSSID." + bssid
        else:
            essid = item["SSID"]["essid"]["$t"]
            essid = essid.strip()
            name  = "BSSID." + essid + '.' + bssid

        for device in indigo.devices.itervalues(filter="self." + deviceTypeId):    
            if device.pluginProps["address"] == address:
                found = True
                break

        if not found:  
            newProps = {
                "deviceTypeId": deviceTypeId,
                "bssid": bssid,
                "essid": essid,
                "name": name,
                "uuid": bssid,
                "address": address
                }
            indigo.server.log ("Adding new BSSID: " + bssid + " " + essid )
            device = self.createdDiscoveredDevice(newProps)
            self.addDeviceToList (device)


        if not(device.states['onOffState']):
            self.debugLog (device.name + " is on") 
            device.updateStateOnServer(key='onOffState', value=True)
        self.deviceList[device.id]['lastTimeSensor'] =  datetime.datetime.now()

        if item["SSID"]["essid"]["cloaked"]:
            self.updateDeviceState(device,'hiddenessid','Yes')
            self.updateDeviceState(device,'essid',essid)
        else:
            self.updateDeviceState(device,'hiddenessid','No')
            self.updateDeviceState(device,'essid',essid)

        self.updateDeviceState(device,'channel',int(item["channel"]))
        if int(item["channel"]) > 14:
            self.updateDeviceState(device,'band5ghz','Yes')
        else:
            self.updateDeviceState(device,'band5ghz','No')
        self.updateDeviceState(device,'manufacturer',item["manuf"])
        self.updateDeviceState(device,'power',int(item["snr-info"]["max_signal_rssi"]))

        try:
            if isinstance(item["wireless-client"],list):
                pass
        except:
            return

        if isinstance(item["wireless-client"],list):
            for i in range(len(item["wireless-client"])):
                client = item["wireless-client"][i]
                self.parseAirodumpClient(client,bssid)
        else:
            client = item["wireless-client"]
            self.parseAirodumpClient(client,bssid)


    def parseAirodumpAdhoc (self,item):
        indigo.server.log ("Ad-hoc: " + item["BSSID"])
        pass

    def parseAirodumpClient(self,client,bssid):
        found    = False
        device   = None
        deviceTypeId = "airodump-station"
        essid       = ""
        macaddress  = client["client-mac"]
        address     = macaddress
        lastbssid   = bssid
        lastessid   = ""
        uuid        = client["client-manuf"]
        

        for device in indigo.devices.itervalues(filter="self." + deviceTypeId):    
            if device.pluginProps["address"] == address:
                found = True
                break

        if not found: 
            name = "Station." + macaddress
            for devicebssid in indigo.devices.itervalues(filter="self.airodump-bssid"):
                    if devicebssid.pluginProps['bssid'] == bssid:
                        name = "Station." + devicebssid.name + "." + macaddress
                        name = name.replace ("BSSID.","")
                        break
            newProps = {
                "deviceTypeId": deviceTypeId,
                "bssid": lastbssid,
                "essid": essid,
                "macaddress": macaddress,
                "name": name,
                "uuid": uuid,
                "address": address
                }
            indigo.server.log ("Adding new Station: " + macaddress + " " + client["client-manuf"])
            device = self.createdDiscoveredDevice(newProps)
            self.addDeviceToList (device)

        if not(device.states['onOffState']):
            self.debugLog (device.name + " is on") 
            device.updateStateOnServer(key='onOffState', value=True)
        self.deviceList[device.id]['lastTimeSensor'] =  datetime.datetime.now()

        if lastbssid == macaddress:
            self.updateDeviceState(device,'associated','No')
            lastbssid = ""
            lastessid = ""
        else:
            self.updateDeviceState(device,'associated','Yes')
            deviceBssid = None
            for deviceBssid in indigo.devices.itervalues(filter="self.airodump-bssid"):    
                if deviceBssid.pluginProps["bssid"] == bssid:
                    lastessid = deviceBssid.states["essid"]
                    break

        self.updateDeviceState(device,'lastbssid',lastbssid)
        self.updateDeviceState(device,'lastessid',lastessid)
        self.updateDeviceState(device,'manufacturer',client["client-manuf"])
        #self.updateDeviceState(device,'power',int(item["snr-info"]["max_signal_rssi"]))

    def parseAirodumpProbe (self,item):

        found        = False
        associated   = False
        device       = None
        deviceTypeId = "airodump-station"

        essid        = ""
        macaddress   = item["wireless-client"]["client-mac"]
        address      = macaddress
        lastbssid    = item["BSSID"]
        uuid         = item["wireless-client"]["client-manuf"]
        
        if lastbssid == macaddress:
            associated = False
        else:
            associated = True

        if not (associated):
            return

        for device in indigo.devices.itervalues(filter="self." + deviceTypeId):    
            if device.pluginProps["address"] == address:
                found = True
                break

        if not found:  
            newProps = {
                "deviceTypeId": deviceTypeId,
                "bssid": lastbssid,
                "essid": essid,
                "macaddress": macaddress,
                "name": "Probe." + macaddress ,
                "uuid": uuid,
                "address": address
                }
            indigo.server.log ("Adding new Station: " + macaddress + " " + item["wireless-client"]["client-manuf"])
            device = self.createdDiscoveredDevice(newProps)
            self.addDeviceToList (device)

        if not(device.states['onOffState']):
            self.debugLog (device.name + " is on") 
            device.updateStateOnServer(key='onOffState', value=True)

        self.deviceList[device.id]['lastTimeSensor'] =  datetime.datetime.now()

        
        self.updateDeviceState(device,'manufacturer',item["wireless-client"]["client-manuf"])
        self.updateDeviceState(device,'power',int(item["snr-info"]["max_signal_rssi"]))
        if lastbssid == macaddress:
            self.updateDeviceState(device,'associated','No')
        else:
            self.updateDeviceState(device,'associated','Yes')
            self.updateDeviceState(device,'lastbssid',lastbssid)
            

    def createdDiscoveredDevice(self,props):
        deviceFolderId = self.getDiscoveryFolder()
        fullName = self.getDiscoveryDeviceName (props['name'],props['uuid'])
        if props["deviceTypeId"] == "airodump-bssid":
            deviceProps={"bssid": props['bssid']}
        elif props["deviceTypeId"] == "airodump-station":
            deviceProps={"macaddress": props['macaddress']}
        else:
            pass
        device = indigo.device.create(protocol=indigo.kProtocol.Plugin,
                        address=props['address'],
                        name=fullName , 
                        description='Airodump discovered device', 
                        pluginId="com.tenallero.indigoplugin.nicewifi",
                        deviceTypeId=props["deviceTypeId"],
                        props=deviceProps,
                        folder=deviceFolderId)
        self.addDeviceToList (device)
        return device
        
        
    def getDiscoveryFolder (self):
        deviceFolderName = "Airodump"
        if (deviceFolderName not in indigo.devices.folders):
            newFolder = indigo.devices.folder.create(deviceFolderName)
            indigo.devices.folder.displayInRemoteUI(newFolder, value=False)
            indigo.server.log ('Created new device folder "Airodump"')
        deviceFolderId = indigo.devices.folders.getId(deviceFolderName)
        return deviceFolderId
        
    def getDiscoveryDeviceName(self,name,uuid):
        if not self.deviceNameExists(name):
            return name
        seedName = name + '-' + uuid
        newName = seedName
        duplicated = 0
        while True:                    
            if not self.deviceNameExists(newName):
                break
            duplicated += 1
            newName = seedName + ' (' + str(duplicated) + ')'
        return newName
                 
    def deviceNameExists (self,name):
        nameFound = False
        for device in indigo.devices:
            if device.name == name:
                nameFound = True
                break
        return nameFound

    ###################################################################
    # HTTP Server
    ###################################################################
   
    def startHTTPServer(self):        
        try:  
            self.myThread = threading.Thread(target=self.listenHTTP, args=())
            self.myThread.daemon = True
            self.myThread.start() 
        except Exception, e:
            self.plugin.errorLog(u"HTTP Server: Error: " + str(e))
     
    def listenHTTP(self):
        self.debugLog(u"Starting HTTP server on port " + str(self.httpServerPort))
        try:        
            self.server = ThreadedHTTPServer(('', self.httpServerPort), lambda *args: httpHandler(self, *args))
            self.server.serve_forever()
        except Exception, e:
            self.plugin.errorLog(u"HTTP Server: Error: " + str(e))
                    
    ###################################################################
    # Concurrent Thread
    ###################################################################

    def runConcurrentThread(self):
        self.debugLog(u"Starting Concurrent thread")
        try:
            while not self.stopThread:
                if self.CreatingDevice:
                    pass
                else:
                    airodumpRun = False
                    interval    = 300
                    for device in indigo.devices.itervalues(filter="self.airodump"):
                        if device.states['onOffState']:
                            airodumpRun = True
                            interval = 3 * int(device.pluginProps["interval"])
                            break
                    if airodumpRun: 
                        todayNow = datetime.datetime.now()
                        lastTime = todayNow - datetime.timedelta(seconds=interval)
                        for deviceId in self.deviceList:
                            device = indigo.devices[deviceId]
                            if device.states['onOffState'] == True:
                                if (self.deviceList[deviceId]['lastTimeSensor'] < lastTime):      
                                    self.debugLog (device.name + " turn off")            
                                    device.updateStateOnServer(key='onOffState', value=False)

                self.sleep(1)

        except self.StopThread:
            # cleanup
            pass
        self.debugLog(u"Exited Concurrent thread")

    def stopConcurrentThread(self):
        self.stopThread = True
        self.debugLog(u"stopConcurrentThread called")

    def updateDeviceState(self,device,state,newValue):
        if (newValue != device.states[state]):
            device.updateStateOnServer(key=state, value=newValue)

    ###################################################################
    # TurnOn/TurnOff
    ###################################################################


    def TurnOnAiroDump(self,device):
        #self.debugLog ( "TurnOnAiroDump called") 
        if device.states['onOffState'] == True:
            self.debugLog ("The device was turned on.") 
            return
        
        ipaddress   = device.pluginProps["address"]
        login       = device.pluginProps["login"]
        password    = device.pluginProps["password"]        
        interval    = int(device.pluginProps["interval"])
        if not(interval > 1):
            interval = 30
        try:
            session = pxssh.pxssh()        
            if not session.login (ipaddress, login, password): 
                self.errorLog ("SSH session failed on login.") 
                self.debugLog (str(session))
                device.updateStateOnServer(key='onOffState', value=False)
            else: 
                
                self.debugLog    ("SSH session login successful") 
                self.debugLog    ("Restarting interface ...") 
                session.sendline ('kill -9 $(pidof airodump-ng) ');session.prompt()
                session.sendline ('kill -9 $(pidof airodump-ng) ');session.prompt()
                session.sendline ('kill -9 $(pidof aireplay-ng) ');session.prompt()
                session.sendline ('kill -9 $(pidof aireplay-ng) ');session.prompt()
                session.sendline ('airmon-ng stop wlan0mon');session.prompt()
                session.sendline ('aircrack-ng check kill');session.prompt()
                session.sendline ('airmon-ng check kill ');session.prompt()
                session.sendline ('airmon-ng start wlan0');session.prompt()
                session.sendline ('cd /root/node-airodump-parser/data');session.prompt()
                session.sendline ('rm dump*');session.prompt()
                self.debugLog    ("Starting airodump-ng ...") 
                session.sendline ('(airodump-ng --band abg --manufacturer --wps --output-format netxml --write-interval ' + str(interval) + ' --write dump wlan0mon >/dev/null 2>&1) &');session.prompt()
                session.sendline ('cd /root/node-airodump-parser/');session.prompt()
                self.debugLog    ("Starting nodejs ...") 
                session.sendline ('(NODE_ENV=dev node app.js >/dev/null 2>&1) &');session.prompt()
                session.logout()
                self.debugLog ( "SSH session logout successful") 
                device.updateStateOnServer(key='onOffState', value=True)
                indigo.server.log(device.name + u": Turned on successfully")
        except Exception, e:
            self.errorLog(u"SSH session: Error: " + str(e))
            device.updateStateOnServer(key='onOffState', value=False)
            return

    def TurnOffAiroDump(self,device):
        #self.debugLog ( "TurnOffAiroDump called")

        ipaddress   = device.pluginProps["address"]
        login       = device.pluginProps["login"]
        password    = device.pluginProps["password"]        

        try:
            session = pxssh.pxssh()        
            if not session.login (ipaddress, login, password): 
                self.errorLog ("SSH session failed on login.") 
                self.debugLog (str(session))                
            else: 
                self.debugLog ( "SSH session login successful") 
                self.debugLog ( "sending commands ...") 
                session.sendline ('kill -9 $(pidof airodump-ng) ');session.prompt()
                session.sendline ('kill -9 $(pidof node) ');session.prompt()
                session.sendline ('airmon-ng stop wlan0mon ; aircrack-ng check kill ; airmon-ng check kill ');session.prompt()
                session.sendline ('cd /root/node-airodump-parser/data ; rm dump*');session.prompt()
                self.debugLog ( "Logout ...")  
                session.logout()
                self.debugLog ( "SSH session logout successful") 
                device.updateStateOnServer(key='onOffState', value=False)
                indigo.server.log(device.name + u": Turned off successfully")
        except Exception, e:
            self.errorLog(u"SSH session: Error: " + str(e))
            device.updateStateOnServer(key='onOffState', value=False)
            return

    def TurnOnAireplay(self,device):
        #self.debugLog ( "TurnOnAireplay called") 
        if device.states['onOffState'] == True:
            self.debugLog ("The device was turned on.") 
            return
        
        ipaddress   = device.pluginProps["address"]
        login       = device.pluginProps["login"]
        password    = device.pluginProps["password"]        
        bssiddevice = int(device.pluginProps["bssiddevice"])
        
        bssid       = indigo.devices[bssiddevice].pluginProps["bssid"]
        channel     = int(indigo.devices[bssiddevice].states["channel"])

        if channel > 14:
            band = 'a'
        else:
            band = 'g'
        airodumpcmd = "airodump-ng --band " + band + " -c " + str(channel) + " wlan0mon"
        aireplaycmd = "aireplay-ng -0 0 -a " + bssid  + " wlan0mon"

        airodumpcmd = airodumpcmd + ' >/dev/null 2>&1' 
        aireplaycmd = aireplaycmd + ' >/dev/null 2>&1' 
        airodumpcmd = '(' + airodumpcmd + ') &'
        aireplaycmd = '(' + aireplaycmd + ') &'

        try:
            session = pxssh.pxssh()        
            if not session.login (ipaddress, login, password): 
                self.errorLog ("SSH session failed on login.") 
                self.debugLog (str(session))
                device.updateStateOnServer(key='onOffState', value=False)
            else: 
                self.debugLog    ("SSH login successful") 
                self.debugLog    ("restarting interface ...") 
                session.sendline ('kill -9 $(pidof airodump-ng) ') ;session.prompt()
                session.sendline ('kill -9 $(pidof airodump-ng) ') ;session.prompt()
                session.sendline ('kill -9 $(pidof airodump-ng) ') ;session.prompt()
                session.sendline ('kill -9 $(pidof aireplay-ng) ') ;session.prompt()
                session.sendline ('kill -9 $(pidof aireplay-ng) ') ;session.prompt()
                session.sendline ('kill -9 $(pidof aireplay-ng) ') ;session.prompt()
                session.sendline ('airmon-ng stop wlan0mon')    ;session.prompt()
                session.sendline ('aircrack-ng check kill')     ;session.prompt()
                session.sendline ('airmon-ng check kill ')      ;session.prompt()
                session.sendline ('airmon-ng start wlan0')      ;session.prompt()  
                self.debugLog    ("Tuning airmon-ng to channel " + str(channel)) 
                self.debugLog    ("Sending command " + airodumpcmd)               
                session.sendline (airodumpcmd);session.prompt() 
                
                self.debugLog    ("Wait for 2 seconds")   
                session.sendline ('sleep 2')
                self.sleep(2.5) ;session.prompt()    
                session.sendline ('kill -9 $(pidof airodump-ng) ');session.prompt()
                self.debugLog    ("Starting aireplay") 
                self.debugLog    ("Sending command " + aireplaycmd)  
                session.sendline (aireplaycmd) ;session.prompt()
                session.sendline ('cd /')      ;session.prompt()
                self.sleep(1)
                self.debugLog    ("Logout ...")  
                session.logout()
                self.debugLog    ("SSH logout successful") 
                device.updateStateOnServer(key='onOffState', value=True)
                indigo.server.log(device.name + u": Turned on successfully")
        except Exception, e:
            self.errorLog(u"SSH session: Error: " + str(e))
            device.updateStateOnServer(key='onOffState', value=False)
            return

    def TurnOffAireplay(self,device):
        #self.debugLog ( "TurnOffAireplay called")

        ipaddress   = device.pluginProps["address"]
        login       = device.pluginProps["login"]
        password    = device.pluginProps["password"]        

        try:
            session = pxssh.pxssh()        
            if not session.login (ipaddress, login, password): 
                self.errorLog ("SSH session failed on login.") 
                self.debugLog (str(session))                
            else: 
                self.debugLog ( "SSH session login successful") 
                self.debugLog ( "sending commands ...") 
                session.sendline ('kill -9 $(pidof aireplay-ng) ');session.prompt()
                session.sendline ('kill -9 $(pidof aireplay-ng) ');session.prompt()
                session.sendline ('kill -9 $(pidof airodump-ng) ');session.prompt()
                session.sendline ('airmon-ng stop wlan0mon');session.prompt()
                session.sendline ('aircrack-ng check kill');session.prompt()
                session.sendline ('airmon-ng check kill ');session.prompt()
                session.logout()
                self.debugLog ( "SSH session logout successful") 
                device.updateStateOnServer(key='onOffState', value=False)
                indigo.server.log(device.name + u": Turned off successfully")
        except Exception, e:
            self.errorLog(u"SSH session: Error: " + str(e))            
            return

    def TurnOnNanoStation(self,device):
        #self.debugLog ( "TurnOnNanoStation called") 
        if device.states['onOffState'] == True:
            self.debugLog ("The device was turned on.") 
            return
        
        ipaddress   = device.pluginProps["address"]
        login       = device.pluginProps["login"]
        password    = device.pluginProps["password"]        
        bssiddevice = int(device.pluginProps["bssiddevice"])
        
        bssid       = indigo.devices[bssiddevice].pluginProps["bssid"]
        channel     = int(indigo.devices[bssiddevice].states["channel"])
        essid       = indigo.devices[bssiddevice].states["essid"]
        hiddenessid = indigo.devices[bssiddevice].states["hiddenessid"]

        freq = 0

        try:
            session = pxssh.pxssh()        
            if not session.login (ipaddress, login, password): 
                self.errorLog ("SSH session failed on login.") 
                self.debugLog (str(session))
                device.updateStateOnServer(key='onOffState', value=False)
            else: 
                
                self.debugLog ( "SSH login successful") 
                self.debugLog ( "sending commands ...")
                #session.sendline ('iwconfig ath0 channel ' + str(channel));session.prompt() 
                #session.sendline ('iwconfig ath0 freq 5.600G');session.prompt()
                #session.sendline ('iwconfig ath0 essid "' + essd + '"');session.prompt()
                #session.sendline ('cfgmtd -f /tmp/system.cfg -w');session.prompt()
                
                session.logout()
                self.debugLog ( "SSH logout successful") 
                device.updateStateOnServer(key='onOffState', value=True)
                indigo.server.log(device.name + u": Turned on successfully")
        except Exception, e:
            self.errorLog(u"SSH session: Error: " + str(e))
            device.updateStateOnServer(key='onOffState', value=False)
            return

    def TurnOffNanoStation(self,device):
        device.updateStateOnServer(key='onOffState', value=False)
        indigo.server.log(device.name + u": Turned off successfully")
        pass

    ###################################################################
    # Custom Action callbacks
    ###################################################################

    def TurnOn(self, pluginAction, device):
        if not (device):
            return False
        #indigo.server.log(device.name + u": TurnOn Action called")
        if device.deviceTypeId == "airodump":
            self.TurnOnAiroDump(device)
            return True
        elif device.deviceTypeId == "aireplay":
            self.TurnOnAireplay(device)
            return True
        elif device.deviceTypeId == "nanostation":
            self.TurnOnNanoStation(device)
            return True

    def TurnOff(self, pluginAction, device):
        if not(device):
            return False
        #indigo.server.log(device.name + u": TurnOff Action called")
        if device.deviceTypeId == "airodump":
            self.TurnOffAiroDump(device)
            return True
        elif device.deviceTypeId == "aireplay":
            self.TurnOffAireplay(device)
            return True
        elif device.deviceTypeId == "nanostation":
            self.TurnOffNanoStation(device)
            return True

    ###################################################################
    # Relay Action callbacks    
    ###################################################################

    def actionControlDimmerRelay(self, pluginAction, device):
        ## Relay ON ##
        if pluginAction.deviceAction == indigo.kDeviceAction.TurnOn:
            indigo.server.log(u"sent \"%s\" %s" % (device.name, "on"))
            if not self.TurnOn(pluginAction,device):        
                indigo.server.log(u"send \"%s\" %s failed" % (device.name, "on"), isError=True)

        ## Relay OFF ##
        elif pluginAction.deviceAction == indigo.kDeviceAction.TurnOff:
            indigo.server.log(u"sent \"%s\" %s" % (device.name, "off"))
            if not self.TurnOff(pluginAction,device):             
                indigo.server.log(u"send \"%s\" %s failed" % (device.name, "off"), isError=True)

        ## Relay TOGGLE ##
        elif pluginAction.deviceAction == indigo.kDeviceAction.Toggle:
            if device.onState:
                self.TurnOff(pluginAction,device)
            else:
                self.TurnOn(pluginAction,device)

        ## Relay Status Request ##
        elif pluginAction.deviceAction == indigo.kDeviceAction.RequestStatus:
            indigo.server.log(u"sent \"%s\" %s" % (device.name, "status request"))
            #if not(self.sensorUpdate (device,True)):
            #    self.errorLog(u"\"%s\" %s" % (device.name, "status request failed"))

    ########################################
    # Menu Methods
    ########################################
    def toggleDebugging(self):
        if self.debug:
            indigo.server.log("Turning off debug logging")
            self.pluginPrefs["debugEnabled"] = False                
        else:
            indigo.server.log("Turning on debug logging")
            self.pluginPrefs["debugEnabled"] = True
        self.debug = not self.debug
        return
   
    def checkForUpdates(self):
        update = self.updater.checkForUpdate() 
        if (update != None):
            pass
        return    

    def updatePlugin(self):
        self.updater.update()
