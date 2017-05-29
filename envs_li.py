import cv2
from gym.spaces.box import Box
import numpy as np
import numpy
import gym
from gym import spaces
import logging
import universe
from universe import vectorized
from universe.wrappers import BlockingReset, GymCoreAction, EpisodeID, Unvectorize, Vectorize, Vision, Logger
from universe import spaces as vnc_spaces
from universe.spaces.vnc_event import keycode
import time
import scipy.io as sio
import matplotlib.pyplot as plt
from math import radians, cos, sin, asin, sqrt, log
import math
import copy
from mpl_toolkits.mplot3d import Axes3D
import scipy
import scipy.cluster.hierarchy as sch
from scipy.cluster.vq import vq,kmeans,whiten
import subprocess
import urllib
from mpl_toolkits.mplot3d import Axes3D
from matplotlib import cm
from matplotlib.ticker import LinearLocator, FormatStrFormatter
from vrplayer import get_view
from move_view_lib import move_view
from suppor_lib import *

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
universe.configure_logging()

class env_li():

    '''
    Function: env interface for ff
    Coder: syh
    Status: checking
    '''

    def __init__(self, env_id, task):

        '''only log if the task is on zero and cluster is the main cluster'''
        self.task = task

        '''get id contains only name of the video'''
        self.env_id = env_id

        '''load config'''
        self.config()

        '''reset'''
        self.observation = self.reset()

    def get_observation(self):

        '''interface to get view'''
        self.cur_observation = get_view(input_width=self.video_size_width,
                                        input_height=self.video_size_heigth,
                                        view_fov_x=self.view_range_lon,
                                        view_fov_y=self.view_range_lat,
                                        cur_frame=self.cur_frame,
                                        is_render=False,
                                        output_width=np.shape(self.observation_space)[0],
                                        output_height=np.shape(self.observation_space)[1],
                                        view_center_lon=self.cur_lon,
                                        view_center_lat=self.cur_lat,
                                        temp_dir=self.temp_dir,
                                        file_='../../vr/' + self.env_id + '.yuv')

    def config(self):

        '''function to load config'''
        print("=================config=================")

        '''observation_space'''
        from config import observation_space
        self.observation_space = observation_space

        from config import num_workers_global,cluster_current,cluster_main
        if (self.task%num_workers_global==0) and (cluster_current==cluster_main):
            self.log_thread = True
        else:
            self.log_thread = False

        '''set all temp dir for this worker'''
        self.temp_dir = "temp/get_view/w_" + str(self.task) + '/'
        print(self.task)
        print(self.temp_dir)
        '''clear temp dir for this worker'''
        subprocess.call(["rm", "-r", self.temp_dir])
        subprocess.call(["mkdir", "-p", self.temp_dir])

        '''load in mat data of head movement'''
        matfn = '../../vr/FULLdata_per_video_frame.mat'
        data_all = sio.loadmat(matfn)
        data = data_all[self.env_id]
        self.subjects_total = get_num_subjects(data=data)

        print("env set to: "+str(self.env_id))

        '''frame bug'''
        '''some bug in the frame read for some video,='''
        if(self.env_id=='Dubai'):
            self.frame_bug_offset = 540
        elif(self.env_id=='MercedesBenz'):
            self.frame_bug_offset = 10
        elif(self.env_id=='Cryogenian'):
            self.frame_bug_offset = 10
        else:
            self.frame_bug_offset = 0

        '''get subjects'''
        self.subjects_total, self.data_total, self.subjects, _ = get_subjects(data,0)

        '''init video and get paramters'''
        video = cv2.VideoCapture('../../vr/' + self.env_id + '.mp4')
        self.frame_per_second = video.get(cv2.cv.CV_CAP_PROP_FPS)
        self.frame_total = video.get(cv2.cv.CV_CAP_PROP_FRAME_COUNT)
        self.video_size_width = int(video.get(cv2.cv.CV_CAP_PROP_FRAME_WIDTH))
        self.video_size_heigth = int(video.get(cv2.cv.CV_CAP_PROP_FRAME_HEIGHT))
        self.second_total = self.frame_total / self.frame_per_second
        self.data_per_frame = self.data_total / self.frame_total

        '''compute step lenth from data_tensity'''
        from config import data_tensity
        self.second_per_step = max(data_tensity/self.frame_per_second, data_tensity/self.data_per_frame/self.frame_per_second)
        self.frame_per_step = self.frame_per_second * self.second_per_step
        self.data_per_step = self.data_per_frame * self.frame_per_step

        '''compute step_total'''
        self.step_total = int(self.data_total / self.data_per_step) + 1

        '''set fov range'''
        from config import view_range_lon, view_range_lat
        self.view_range_lon = view_range_lon
        self.view_range_lat = view_range_lat

        '''cc'''
        self.cc_count_to = self.subjects_total
        self.agent_result_saver = np.zeros((self.step_total, 2))
        self.agent_result_stack = []

        '''salmap'''
        self.heatmap_height = 180
        self.heatmap_width = 360

        from config import if_log_scan_path
        self.if_log_scan_path = if_log_scan_path and self.log_thread

        self.episode = 0
        self.cur_cc = 0.0

        '''load ground-truth heat map'''
        self.gt_heatmaps = self.load_heatmaps('gt_heatmap_sp_my_sigma')

        self.max_cc = 0.0
        self.cur_cc = 0.0

    def load_heatmaps(self, name):

        heatmaps = []
        for step in range(self.step_total):

            try:
                file_name = '../../vr/'+name+'/'+self.env_id+'_'+str(step)+'.jpg'
                temp = cv2.imread(file_name, cv2.CV_LOAD_IMAGE_GRAYSCALE)
                temp = cv2.resize(temp,(self.heatmap_width, self.heatmap_height))
                temp = temp / 255.0
                heatmaps += [temp]
            except Exception,e:
                print Exception,":",e
                continue

        print('load heatmaps: '+name+' done, size: '+str(np.shape(heatmaps)))

        return heatmaps

    def reset(self):

        '''reset cur_step and cur_data'''
        self.cur_step = 0
        self.cur_data = 0

        '''episode add'''
        self.episode +=1

        '''reset cur_frame'''
        self.cur_frame = 0

        '''reset cur_lon and cur_lat to one of the subjects start point'''
        subject_dic_code = []
        for i in range(self.subjects_total):
            subject_dic_code += [i]
        subject_code = np.random.choice(a=subject_dic_code)
        self.cur_lon = self.subjects[subject_code].data_frame[0].p[0]
        self.cur_lat = self.subjects[subject_code].data_frame[0].p[1]

        '''set observation_now to the first frame'''
        self.get_observation()

        self.last_observation = None

        return self.cur_observation

    def save_heatmap(self,heatmap,path,name):
        heatmap = heatmap * 255.0
        cv2.imwrite(path+'/'+name+'.jpg',heatmap)

    def step(self, action):

        '''log_scan_path'''
        if self.if_log_scan_path is True:
            plt.figure(str(self.env_id)+'_scan_path')
            subject_code=1
            if(self.cur_lon>180):
                draw_lon = self.cur_lon - 360.0
            else:
                draw_lon = self.cur_lon
            plt.scatter(draw_lon, self.cur_lat, c='r')
            plt.scatter(-180, -90)
            plt.scatter(-180, 90)
            plt.scatter(180, -90)
            plt.scatter(180, 90)
            plt.pause(0.1)

        if(self.cur_lon>180):
            draw_lon = self.cur_lon - 360.0
        else:
            draw_lon = self.cur_lon
        draw_lat = self.cur_lat

        self.agent_result_saver[self.cur_step, 0] = draw_lon
        self.agent_result_saver[self.cur_step, 1] = draw_lat

        '''varible for record state is stored, for they will be updated'''
        self.last_step = self.cur_step
        self.last_data = self.cur_data
        self.last_observation = self.cur_observation
        self.last_lon = self.cur_lon
        self.last_lat = self.cur_lat
        self.last_frame = self.cur_frame

        '''update cur_step'''
        self.cur_step += 1

        '''update cur_data'''
        self.cur_data = int(round((self.cur_step)*self.data_per_step))
        if(self.cur_data>=self.data_total):
            update_data_success = False
        else:
            update_data_success = True

        '''update cur_frame'''
        self.cur_frame = int(round((self.cur_step)*self.frame_per_step))
        if(self.cur_frame>=(self.frame_total-self.frame_bug_offset)):
            update_frame_success = False
        else:
            update_frame_success = True

        '''if any of update frame or update data is failed'''
        if(update_frame_success==False)or(update_data_success==False):

            '''terminating'''
            self.reset()
            reward = 0.0
            done = True

            if self.if_log_scan_path is True:
                plt.figure(str(self.env_id)+'_scan_path')
                plt.clf()

            self.agent_result_stack += [self.agent_result_saver]
            from config import log_cc_interval
            if len(self.agent_result_stack) > self.subjects_total:

                '''if stack full, pop out the oldest data'''
                self.agent_result_stack.pop(0)

                if self.log_thread is True and self.episode%log_cc_interval is 0:

                    print('compute cc..................')

                    ccs_on_step_i = []
                    heatmaps_on_step_i = []
                    for step_i in range(self.step_total):

                        '''generate predicted salmap'''
                        heatmaps_on_step_i += [fixation2salmap(fixation=np.asarray(self.agent_result_stack)[:,step_i],
                                                               mapwidth=self.heatmap_width,
                                                               mapheight=self.heatmap_height)]
                        from cc import calc_score
                        ccs_on_step_i += [calc_score(self.gt_heatmaps[step_i], heatmaps_on_step_i[step_i])]

                    self.cur_cc = np.mean(np.asarray(ccs_on_step_i))
                    if self.cur_cc > self.max_cc:
                        print('new max cc found: '+str(self.cur_cc)+', recording cc and heatmaps')
                        self.max_cc = self.cur_cc
                        self.heatmaps_of_max_cc = heatmaps_on_step_i

                        from config import final_log_dir
                        record_dir = final_log_dir+'ff_best_heatmaps/'+self.env_id+'/'
                        subprocess.call(["rm", "-r", record_dir])
                        subprocess.call(["mkdir", "-p", record_dir])
                        for step_i in range(self.step_total):
                            self.save_heatmap(heatmap=self.heatmaps_of_max_cc[step_i],
                                              path=record_dir,
                                              name=str(step_i))


        else:

            '''get reward and v from last state'''
            last_prob, distance_per_data = get_prob(lon=self.last_lon,
                                              lat=self.last_lat,
                                              theta=action * 45.0,
                                              subjects=self.subjects,
                                              subjects_total=self.subjects_total,
                                              cur_data=self.last_data)

            '''rescale'''
            distance_per_step = distance_per_data * self.data_per_step

            '''convert v to degree'''
            degree_per_step = distance_per_step / math.pi * 180.0

            '''move view, update cur_lon and cur_lat'''
            self.cur_lon, self.cur_lat = move_view(cur_lon=self.last_lon,
                                                   cur_lat=self.last_lat,
                                                   direction=action,
                                                   degree_per_step=degree_per_step)

            '''update observation_now'''
            self.get_observation()

            '''produce output'''
            reward = last_prob
            done = False

        return self.cur_observation, reward, done, self.cur_cc