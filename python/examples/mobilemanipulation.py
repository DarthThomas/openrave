#!/usr/bin/env python
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import with_statement # for python 2.5
__author__ = 'Rosen Diankov'
__copyright__ = 'Copyright (C) 2009-2010 Rosen Diankov (rosen.diankov@gmail.com)'
__license__ = 'Apache License, Version 2.0'

import time,traceback
from openravepy import *
from openravepy.interfaces import BaseManipulation, TaskManipulation
from openravepy.examples import inversereachability,graspplanning,grasping
from numpy import *
import numpy
from itertools import izip

class GraspReachability(metaclass.AutoReloader):
    def __init__(self,robot,target=None,gmodel=None,irmodel=None):
        self.robot = robot
        self.env = robot.GetEnv()
        self.manip = robot.GetActiveManipulator()
        if gmodel is not None and gmodel.has():
            self.gmodel = gmodel
        else:
            self.gmodel = grasping.GraspingModel(robot=robot,target=target)
            if not self.gmodel.load():
                self.gmodel.autogenerate()
        self.target = self.gmodel.target
        if irmodel is not None and irmodel.has():
            self.irmodel = irmodel
        else:
            self.irmodel = inversereachability.InverseReachabilityModel(robot=robot)
            if not self.irmodel.load():
                self.irmodel.autogenerate()

    def computeGraspDistribution(self,randomgrasps=False,**kwargs):
        """computes distribution of all grasps"""
        validgrasps,validindices = self.gmodel.computeValidGrasps(checkik=False,backupdist=0.01)
        def graspiter():
            for grasp,graspindex in izip(validgrasps,validindices):
                yield self.gmodel.getGlobalGraspTransform(grasp,collisionfree=True),graspindex
        densityfn,samplerfn,bounds = self.irmodel.computeAggregateBaseDistribution(graspiter(),**kwargs)
        return densityfn,samplerfn,bounds,validgrasps

    def sampleGoals(self,samplerfn,N=1,updateenv=False,timeout=inf):
        """samples a base placement and attemps to find an IK solution there.
        samplerfn should return a robot position and an index into gmodel.grasps"""
        goals = []
        numfailures = 0
        starttime = time.time()
        with self.robot:
            while len(goals) < N:
                if time.time()-starttime > timeout:
                    return goals,numfailures
                poses,indices = samplerfn(N-len(goals))
                for pose,index in izip(poses,indices):
                    self.robot.SetTransform(pose)
                    if updateenv:
                        self.env.UpdatePublishedBodies()
                    # before recording failure, validate that base is not in collision
                    if not self.manip.CheckIndependentCollision(CollisionReport()):
                        grasp = self.gmodel.grasps[index]
                        self.gmodel.setPreshape(grasp)
                        q = self.manip.FindIKSolution(self.gmodel.getGlobalGraspTransform(grasp,collisionfree=True),envcheck=True)
                        if q is not None:
                            goals.append((grasp,pose,q))
                        elif self.manip.FindIKSolution(self.gmodel.getGlobalGraspTransform(grasp,collisionfree=True),envcheck=False) is None:
                            numfailures += 1
        return goals,numfailures

    def sampleValidPlacementIterator(self,giveuptime=inf,randomgrasps=False,updateenv=False,randomplacement=False,**kwargs):
        """continues to sample valid goal placements. Returns the robot base position, configuration, and the target grasp from gmodel. Environment should be locked, robot state is saved"""
        def graspiter():
            for grasp,graspindex in self.gmodel.validGraspIterator(checkik=False,randomgrasps=randomgrasps,backupdist=0.01):
                yield self.gmodel.getGlobalGraspTransform(grasp,collisionfree=True),graspindex
        starttime = time.time()
        statesaver = self.robot.CreateRobotStateSaver()
        try:
            baseIterator = self.irmodel.randomBaseDistributionIterator if randomplacement else self.irmodel.sampleBaseDistributionIterator
            for pose,graspindex in baseIterator(Tgrasps=graspiter(),**kwargs):
                self.robot.SetTransform(pose)
                if updateenv:
                    self.env.UpdatePublishedBodies()
                if not self.manip.CheckIndependentCollision(CollisionReport()):
                    grasp = self.gmodel.grasps[graspindex]
                    self.gmodel.setPreshape(grasp)
                    q = self.manip.FindIKSolution(self.gmodel.getGlobalGraspTransform(grasp,collisionfree=True),envcheck=True)
                    if q is not None:
                        values = self.robot.GetJointValues()
                        values[self.manip.GetArmJoints()] = q
                        statesaver.close()
                        yield pose,values,grasp,graspindex
                        statesaver = self.robot.CreateRobotStateSaver()
                        starttime = time.time()
                    else:
                        if not isinf(giveuptime) and time.time()-starttime > giveuptime:
                            raise planning_error('timed out')
        finally:
            statesaver.close()
    def showBaseDistribution(self,thresh=1.0,**kwargs):
        starttime = time.time()
        densityfn,samplerfn,bounds,validgrasps = self.computeGraspDistribution(**kwargs)
        print 'time to build distribution: %fs'%(time.time()-starttime)
        return self.irmodel.showBaseDistribution(densityfn,bounds,self.target.GetTransform()[2,3],thresh=thresh)
    def testSampling(self,**kwargs):
        """random sample goals on the current environment"""
        configsampler = self.sampleValidPlacementIterator(**kwargs)
        basemanip = BaseManipulation(self.robot)
        with RobotStateSaver(self.robot):
            while True:
                try:
                    with self.env:
                        pose,values,grasp,graspindex = configsampler.next()
                        print 'found grasp',graspindex
                        self.robot.SetTransform(pose)
                        self.gmodel.setPreshape(grasp)
                        self.robot.SetJointValues(values[self.manip.GetArmJoints()],self.manip.GetArmJoints())
                        final,traj = basemanip.CloseFingers(execute=False,outputfinal=True)
                        self.robot.SetJointValues(final,self.manip.GetGripperJoints())
                        self.env.UpdatePublishedBodies()
                except planning_error, e:
                    traceback.print_exc(e)
                    continue
                

class MobileManipulationPlanning(graspplanning.GraspPlanning):
    def __init__(self,robot,randomize=False,irmodel=None,**kwargs):
        graspplanning.GraspPlanning.__init__(self, robot=robot,randomize=randomize,**kwargs)
        if irmodel is not None and irmodel.has():
            self.irmodel = irmodel
        else:
            self.irmodel = inversereachability.InverseReachabilityModel(robot=robot)
            if not self.irmodel.load():
                self.irmodel.autogenerate()
        
    def graspAndPlaceObjectMobile(grmodel,dests,showdist=False):
        logllthresh = 2000.0
        Ngoals = 20
        densityfn,samplerfn,bounds,validgrasps = grmodel.computeGraspDistribution(logllthresh=logllthresh)
        if showdist:
            h = self.irmodel.showBaseDistribution(densityfn,bounds,self.target.GetTransform()[2,3],thresh=1.0)
        
        starttime = time.time()
        goals,numfailures = grmodel.sampleGoals(samplerfn,N=Ngoals)
        print 'numgrasps: %d, time: %f, failures: %d'%(len(goals),time.time()-starttime,numfailures)

    def performGraspPlanning(self):
        print 'starting to pick and place random objects'
        while True:
            i = random.randint(len(self.graspables))
            try:
                print 'grasping object %s'%self.graspables[i][0].target.GetName()
                if len(self.graspables[i]) == 2:
                    self.graspables[i].append(GraspReachability(robot=self.robot,gmodel=self.graspables[i][0],irmodel=self.irmodel))

                with self.envreal:
                    self.robot.ReleaseAllGrabbed()
                success = self.graspAndPlaceObjectMobile(grmodel=self.graspables[i][2],dests=self.graspables[i][1])
                print 'success: ',success
            except e:
                print 'failed to grasp object %s'%self.graspables[i][0].target.GetName()
                print e
def run():
    env = Environment()
    try:
        env.SetViewer('qtcoin')
        env.Load('data/lab1.env.xml')
        robot = env.GetRobots()[0]
        env.UpdatePublishedBodies()
        time.sleep(0.1) # give time for environment to update
        self = MobileManipulationPlanning(robot,randomize=True)
        self.performGraspPlanning()
    finally:
        env.Destroy()

if __name__ == "__main__":
    #run()
    print 'not implemented yet'
