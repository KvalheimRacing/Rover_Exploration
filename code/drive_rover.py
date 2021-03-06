# Do the necessary imports
import argparse
import shutil
import base64
from datetime import datetime
import os
import cv2
import numpy as np
import socketio
import eventlet
import eventlet.wsgi
from PIL import Image
from flask import Flask
from io import BytesIO, StringIO
import json
import pickle
import matplotlib.image as mpimg

# Import functions for perception and decision making
from perception import perception_step
from decision import decision_step

sio = socketio.Server()
app = Flask(__name__)

# Read in ground truth map and create 3-channel green version for overplotting
ground_truth = mpimg.imread('../calibration_images/map_bw.jpg')
ground_truth_3d = np.dstack((ground_truth*0, ground_truth, ground_truth*0)).astype(np.float)

# Define RoverState() class to retain rover state parameters
class RoverState():
    def __init__(self):
        self.img = None # Current camera image
        self.pos = None # Current position (x, y)
        self.yaw = None # Current yaw angle
        self.pitch = None # Current pitch angle
        self.roll = None # Current roll angle
        self.vel = None # Current velocity
        self.steer = 0 # Current steering angle
        self.throttle = 0 # Current throttle value
        self.brake = 0 # Current brake value
        self.nav_angles = None # Angles of navigable terrain pixels
        self.nav_dists = None # Distances of navigable terrain pixels
        self.ground_truth = ground_truth_3d # Ground truth worldmap
        self.mode = 'forward' # Current mode (can be forward or stop)
        self.throttle_set = 0.2 # Throttle setting when accelerating
        self.brake_set = 10 # Brake setting when braking
        # The stop_forward and go_forward fields below represent total count
        # of navigable terrain pixels.  This is a very crude form of knowing
        # when you can keep going and when you should stop.  Feel free to
        # get creative in adding new fields or modifying these!
        self.stop_forward = 100 # Threshold to initiate stopping
        self.go_forward = 1000 # Threshold to go forward again
        self.max_vel = 2 # Maximum velocity (meters/second)
        # Image output from perception step
        self.vision_image = np.zeros((160, 320, 3), dtype=np.float) 
        # Worldmap
        self.worldmap = np.zeros((200, 200, 3), dtype=np.float) 

# Initialize our rover 
Rover = RoverState()


# Define telemetry function for what to do with incoming data
@sio.on('telemetry')
def telemetry(sid, data):
    if data:
        global Rover
        print(data.keys())
        # The current speed of the rover in m/s
        Rover.vel = np.float(data["speed"])
        # The current position of the rover
        Rover.pos = np.fromstring(data["position"], dtype=float, sep=',')
        # The current yaw angle of the rover
        Rover.yaw = np.float(data["yaw"])
        # The current yaw angle of the rover
        Rover.pitch = np.float(data["pitch"])
        # The current yaw angle of the rover
        Rover.roll = np.float(data["roll"])
        # The current throttle setting
        Rover.throttle = np.float(data["throttle"])
        # The current steering angle
        Rover.steer = np.float(data["steering_angle"])

        print('speed=',Rover.vel, 'position', Rover.pos, 'throttle', 
            Rover.throttle, 'steer_angle', Rover.steer)
        
        # Get the current image from the center camera of the rover
        imgString = data["image"]
        image = Image.open(BytesIO(base64.b64decode(imgString)))
        Rover.img = np.asarray(image)

        if np.isfinite(Rover.vel):
            
            Rover = perception_step(Rover)
            Rover = decision_step(Rover)

            plotmap = np.rot90(Rover.worldmap.clip(0, 255))
            map_add = cv2.addWeighted(plotmap, 1, Rover.ground_truth, 0.4, 0)
            pil_img = Image.fromarray(map_add.clip(0, 255).astype(np.uint8))
            buff = BytesIO()
            pil_img.save(buff, format="JPEG")
            outImageString1 = base64.b64encode(buff.getvalue()).decode("utf-8")
            
            pil_img = Image.fromarray(Rover.vision_image.astype(np.uint8))
            buff = BytesIO()
            pil_img.save(buff, format="JPEG")
            outImageString2 = base64.b64encode(buff.getvalue()).decode("utf-8")
            
            # The actuation step!  Send commands to the rover!
            # commands = (throttle, brake, steering)
            commands = (Rover.throttle, Rover.brake, Rover.steer)
            send_control(commands, outImageString1, outImageString2)

        else:

            # Send zeros for throttle, brake and steer and empty images
            # if we got a bad velocity value in telemetry
            send_control((0, 0, 0), '', '')
        
        # Save image frame if folder was specified
        if args.image_folder != '':
            timestamp = datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f')[:-3]
            image_filename = os.path.join(args.image_folder, timestamp)
            image.save('{}.jpg'.format(image_filename))
    else:
        sio.emit('manual', data={}, skip_sid=True)


@sio.on('connect')
def connect(sid, environ):
    print("connect ", sid)
    send_control((0, 0, 0), '', '')
    sample_data = {}
    sio.emit(
        "get_samples",
        sample_data,
        skip_sid=True)

def send_control(commands, image_string1, image_string3):
    #if image_string != '':
    data={
        'throttle': commands[0].__str__(),
        'brake': commands[1].__str__(),
        'steering_angle': commands[2].__str__(),
        'inset_image': image_string1,
        'inset_image3': image_string3,
        }

    sio.emit(
        "steer",
        data,
        skip_sid=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Remote Driving')
    parser.add_argument(
        'image_folder',
        type=str,
        nargs='?',
        default='',
        help='Path to image folder. This is where the images from the run will be saved.'
    )
    args = parser.parse_args()
    
    os.system('rm -rf IMG_stream/*')
    if args.image_folder != '':
        print("Creating image folder at {}".format(args.image_folder))
        if not os.path.exists(args.image_folder):
            os.makedirs(args.image_folder)
        else:
            shutil.rmtree(args.image_folder)
            os.makedirs(args.image_folder)
        print("Recording this run ...")
    else:
        print("NOT recording this run ...")
    
    # wrap Flask application with engineio's middleware
    app = socketio.Middleware(sio, app)

    # deploy as an eventlet WSGI server
    eventlet.wsgi.server(eventlet.listen(('', 4567)), app)
