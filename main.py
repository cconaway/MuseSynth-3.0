"""
Dynamo - main.py

Todo - 
    Accelerometers.
    Gyros.
    
    Get things on non competing thread spaces?
"""

#Standard Library
import threading
import queue
import time
import collections

#Third Party
from pythonosc import osc_server
from pythonosc.dispatcher import Dispatcher
from pythonosc.udp_client import SimpleUDPClient
import numpy as np

#Server IP & Port
server_ip = ''
server_port = 8000

client_ip1 = ''
client_port1 = 8001

client_ip2 = ""
client_port2 = 8000




#Making a client
client = SimpleUDPClient(client_ip1, client_port1)
client2 = SimpleUDPClient(client_ip2, client_port2)
#Send to multiple clients

#Create Dispatcher Object
dispatch = Dispatcher()

#Dataqueue for messages
class DataQueue(queue.Queue):
    def __init__(self):
        super().__init__(maxsize=300)
    
    def set_message(self, *args):
        self.put(args)
    
    def get_message(self):
        val = self.get()
        with self.mutex:
            self.queue.clear()
        return val

#Reciever class for interfacing with the dataque.
class Reciever():
    def __init__(self, datalog, name):
        self.datalog = datalog
        self.name = name

    def recieve(self, address:str, *args):
        self.datalog.set_message(args)
        #print(self.name, args, self.datalog.qsize())

#Range Limiter
class RangeLimiter(object):
    
    def __init__(self, input_range, output_range):
        self.outmax = output_range[1]
        self.outmin = output_range[0]
        self.inmax = input_range[1]
        self.inmin = input_range[0]

        self.input_range = (self.inmax - self.inmin)
        self.output_range = (self.outmax - self.outmin)

    def squeeze(self, data):
        output = [(((d-self.inmin) * self.output_range) / self.input_range) + self.outmin for d in data]
        return output

#General Moving Average Object
class MVAverage(object):

    def __init__(self, window=10, numq = 4):
        self.qs = []
        self.window = window
        self.numq = numq

        for i in range(numq):
            self.qs.append(collections.deque())
        self.out = collections.deque()
        
    def run(self, args):
        self.out.clear()

        for q, i in zip(self.qs, range(self.numq)):
            if len(q) == self.window:
                q.popleft()
                q.append(args[i])
            else:
                q.append(args[i])
            self.out.append(sum(q)/len(q))
        
        return tuple(self.out)
        
#Map Alpha
alpha_dataqueue = DataQueue()                           #Set a dedicated queue
alpha_reciever = Reciever(alpha_dataqueue, 'alpha')     #Map queue to reciever
alpha_mv = MVAverage(window=10, numq=4)                 #Make a moving average
alpha_osc_address = "/muse/elements/alpha_absolute"
dispatch.map(alpha_osc_address, alpha_reciever.recieve) #Map to Reciever

#Map Beta
beta_dataqueue = DataQueue()
beta_reciever = Reciever(beta_dataqueue, 'beta')
beta_mv = MVAverage(window=10, numq=4)
beta_osc_address = "/muse/elements/beta_absolute"
dispatch.map(beta_osc_address, beta_reciever.recieve)

#Map HSI
hsi_dataqueue = DataQueue()
hsi_reciever = Reciever(hsi_dataqueue, 'hsi')
dispatch.map('/muse/elements/horseshoe', hsi_reciever.recieve)

#Map Acceleromters
acc_dataqueue = DataQueue()
acc_reciever = Reciever(acc_dataqueue, 'acc')
acc_mv = MVAverage(window=10, numq=3)
dispatch.map('/muse/acc', acc_reciever.recieve)
acc_rangelimiter = RangeLimiter([-2,2], [0,1])

#Output Smoothing
alpha_output_mv = MVAverage(window=10, numq=1)
beta_output_mv = MVAverage(window=10, numq=1)

#Create Server Object
server = osc_server.BlockingOSCUDPServer((server_ip, server_port), dispatch)
#Create Server Thread
server_thread = threading.Thread(target=server.serve_forever, daemon=True)

#Client Function
clients = [client, client2]
def send_to_clients(clients, addr, message):
    for cl in clients:
        cl.send_message(addr, message)
        time.sleep(0.01)


#Alpha Thread Function - What Retrieves Data and Moves it Forward
def wave_proc():
    while True:
        alphas = alpha_mv.run(alpha_dataqueue.get_message()[0]) #Get's 4 streams of EEG.
        betas = beta_mv.run(beta_dataqueue.get_message()[0])
        hsi = hsi_dataqueue.get_message()[0]       #Right now these queues are competing.
        
        alphaval = 0
        betaval = 0
        count = 0

        for hsi_sensor, alpha_sensor, beta_sensor in zip(hsi, alphas, betas):
            if hsi_sensor <= 2.0:
                alphaval += alpha_sensor 
                betaval += beta_sensor 
                count += 1
        try:
            alpha_out = [alphaval/count]
            beta_out = [betaval/count]

        except ZeroDivisionError:
            alpha_out = [0.0]
            beta_out = [0.0]

        alpha_message = alpha_output_mv.run(alpha_out) #Output mv is one value 
        beta_message = beta_output_mv.run(beta_out) 


        send_to_clients(clients, '/alpha_wave', alpha_message)
        send_to_clients(clients, '/beta_wave', beta_message)



#Accelerometer Thread Function
def acc_proc():
    while True:
        acc = acc_rangelimiter.squeeze(acc_mv.run(acc_dataqueue.get_message()[0]))
        send_to_clients(clients, '/acc', acc)




#Create Threads
wave_thread = threading.Thread(target=wave_proc)
acc_thread = threading.Thread(target=acc_proc)

#Start Server Thread
def main():
    server_thread.start()
    print('SERVING ON: ', server_ip, ':', server_port)

    wave_thread.start()

    acc_thread.start()

    for client in clients:
        print('SENDING TO ', client._address, ':', client._port)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print('Done')
