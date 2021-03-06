'''
SDFMap.py
 - Andrew Kramer

 Stores a 2 Dimensional map of an area as a signed distance function.
 Provides functions to access and update the map based on work in

    Fossel, Joscha-David & Tuyls, Karl & Sturm, Jurgen. (2015). 
    2D-SDF-SLAM: A signed distance function based SLAM frontend 
    for laser scanners. 1949-1955. 10.1109/IROS.2015.7353633. 

'''

import numpy as np
import math
import scipy.odr

class SDFMap:

	# initializes the necessary data structures and parameters
	#
	# params:
	# - size:           tuple, stores the starting spatial extents of the map 
	#                   in the x and y directions in meters
	# - discretization: float, the edge length of a single map cell in meters
	# - k:              max radius in which to update vertices in meters
	#
	def __init__(self, size, discretization=0.5, k=3.0):
		self.k = k
		self.disc = discretization
		self.num_x_cells = int(size[0] / self.disc)
		self.num_y_cells = int(size[1] / self.disc)
		self.map = 0.1 * np.ones((self.num_x_cells,self.num_y_cells))
		self.priorities = 100.0 * np.ones((self.num_x_cells,self.num_y_cells))
		self.offsets = np.zeros(2)


	# updates the map using the given laser scan
	# assumes new scan falls completely within the map bounds
	# need to add an "expand map" function to correct this
	#
	# params:
	# - scan: list of scan endpoints in the robot's local coordinate frame
	#         scan endpoints represented as homogeneous 2D cartesian points
	# - pose: the robot's estimated pose at the time of the scan
	#         represented as a 3x3 transformation matrix
	def UpdateMap(self, scan, pose):

		# first transform scan endpoints to global frame
		global_scan = np.dot(scan, pose.T)

		# find groups of points that occupy the same map cell
		point_groups = self.GroupPointsByCell(global_scan)

		# generate map updates for each cell group
		for group in point_groups:

			A,b = self.LinearFit(global_scan, group, pose)

			vertices = self.GetUpdateVertices(A, group[0])

			updates,new_priorities = self.GetDistAndPriority(vertices, A, b, pose, group[0])


			# update vertices based on update priority
			for update_idx in range(0,len(vertices)):

				vertex = vertices[update_idx]

				old_priority = self.GetPriority(vertex[0],vertex[1])
				new_priority = new_priorities[update_idx]
				new_distance = updates[update_idx]

				# if update has higher priority, discard the old measurement
				if new_priority < old_priority:
					self.SetMapValue(vertex[0],vertex[1],new_distance)
					self.SetPriority(vertex[0],vertex[1],new_priority)
				# if update has same priority, average the measurements
				elif new_priority == old_priority:
					old_distance = self.GetMapValue(vertex[0],vertex[1])
					mean_distance = (new_distance + old_distance) / 2.0
					self.SetMapValue(vertex[0],vertex[1],mean_distance)
					
				# if update has lower priority, discard the new measurement

	# Expands the map if necessary to fit the given point
	# params: 
	# - point:  np array, list, or tuple representing a point in the map space
	def ExpandMap(self, x, y):

		point = np.array([x,y])

		# check if we need to expand the map before updating
		for axis in range(2):

			direction = 0
			amount = 0

			if point[axis] + self.offsets[axis] < 0.0:
				direction = -1
				amount = int(abs(point[axis] + self.offsets[axis])) + 1
			elif point[axis] + self.offsets[axis] >= float(self.map.shape[axis]):
				direction = 1
				amount = int(point[axis] + self.offsets[axis]) - self.map.shape[axis] + 1


			for i in range(amount):
				if direction < 0:
					self.map = np.insert(self.map,0,0.1,axis=axis)
					self.priorities = np.insert(self.priorities,0,100,axis=axis)
					self.offsets[axis] += 1
				else:
					self.map = np.insert(self.map,self.map.shape[axis],0.1,axis=axis)
					self.priorities = np.insert(self.priorities,self.priorities.shape[axis],100,axis=axis)



	# calculates the shortest distance to an obstacle and the update priority
	# for the given list of vertices
	# params:
	# - vertices: list of vertices for which to calculate updates
	# - A:        slope of the line fitted to the scan endpoints in the cell
	# - b:        y intercept of the line fitted to the scan endpoints in the cell
	# - pose:     pose of the robot
	# - point:    a point in the group that triggered the update
	# returns:
	# - updates:    list of updated distances for each vertex
	# - priorities: list of priorities for each update
	def GetDistAndPriority(self, vertices, A, b, pose, point):

		updates = []
		priorities = []

		for vertex in vertices:

			# get orthogonal distance between vertex and line
			b_1 = vertex[1] - (A * vertex[0])
			y_diff = (b - b_1) * (1 - (A**2/(A**2+1)))
			x_diff = (b_1 - b) * (A / (A**2 + 1))
			dist = math.sqrt(y_diff**2 + x_diff**2)

			# change sign to negative if the vertex lies on the opposite side
			# of the line from the robot's current pose

			# get distance between robot pose and current vertex
			pose_x = pose[0][2] / self.disc
			pose_y = pose[1][2] / self.disc
			cell_dist_x = vertex[0] - pose_x
			cell_dist_y = vertex[1] - pose_y
			cell_dist = math.sqrt(cell_dist_x**2 + cell_dist_y**2)

			# get line from robot pose to current vertex
			A_p = 1000.0
			if cell_dist_x != 0.0:
				A_p = cell_dist_y / cell_dist_x
			if cell_dist_x < 0:
				A_p *= -1.0

			b_p = pose_y - (A_p * pose_x)

			# get point where line from robot pose to the current vertex intersects
			# with the line fitted to the scan endpoints in the current cell
			x_p = (b_p - b) / (A - A_p)
			y_p = A_p * x_p + b_p

			# distance from robot pose to fitted line along ray from robot pose
			# to current vertex
			line_dist = math.sqrt((pose_x - x_p)**2 + (pose_y - y_p)**2)

			
			# if updated vertex is further away than the fitted line,
			# distance update should be negative
			if line_dist < cell_dist:
				dist *= -1.0

			updates.append(dist * self.disc)

			# get update priority as the min layers of vertices between the current
			# vertex and the point that triggered the update
			x_min,x_max,y_min,y_max = self.GetBoundingVertices(point)
			
			p = max((min((abs(x_min - vertex[0])),abs(x_max - vertex[0])),
				min((abs(y_min - vertex[1])),(abs(y_max - vertex[1])))))
			#p = max(0,min(min(abs(x_min-vertex[0]),abs(x_max-vertex[0])),
			#	min(abs(y_min-vertex[1]),abs(y_max-vertex[1]))))

			priorities.append(p)

		return updates, priorities



	# get list of vertex indices to update per requirements 
	# in section III.A of Fossel et al
	# params:
	# - A:      slope of line fitted to points in cell
	# - point:  a point inside the cell
	# returns:
	# - indices: list of tuples; vertex indices to be updated
	def GetUpdateVertices(self, A, point):

		# get vertices bounding the current cell
		x,y = self.PointToMapCoordinates(point)
		x_min,x_max,y_min,y_max = self.GetBoundingVertices(point)
		x_c = (x_max + x_min) / 2.0
		y_c = (y_max + y_min) / 2.0

		# get lines bounding the cells to update
		A_prime = -1.0 * (1.0 / A) # slope of both bounding lines
		b_lower = 0.0 # y intercept of lower line in cells (not meters)
		b_upper = 0.0 # y intercept of upper line in cells (not meters)
		if A_prime < 0:
			b_lower = y_min - (A_prime * x_min)
			b_upper = y_max - (A_prime * x_max)
		else:
			b_lower = y_min - (A_prime * x_max)
			b_upper = y_max - (A_prime * x_min)

		# search nearby vertices and create list of vertices to update
		# assumes updated cells never go off the edge of the map
		# need to make function to expand map in this case
		indices = []
		k_cells = int(self.k / self.disc)
		x_range_min = int(math.floor(x_c - k_cells))
		x_range_max = int(math.ceil(x_c + k_cells))
		y_range_min = int(math.floor(y_c - k_cells))
		y_range_max = int(math.ceil(y_c + k_cells))
		for x in range(x_range_min, x_range_max):
			for y in range(y_range_min, y_range_max):

				# rule out vertices based on distance to cell center
				dist = math.sqrt((x - x_c)**2 + (y - y_c)**2)

				if dist >= k_cells:
					continue

				# rule out cells not between the two bounding lines
				Ax = A_prime * x
				if Ax + b_upper < y or Ax + b_lower > y:
					continue

				indices.append(np.array([x,y]))

		return indices


	# fits a line to the given list of points using orthogonal regression
	# if the list contains only one point, the line is perpendicular to 
	# the line between that point and the robot's current point
	# params:
	# - points: group of points in the global frame that occupy the same map cell
	# - pose:   robot's current pose expressed as a transformation matrix
	# returns:
	# - A: the slope of the fitted line
	# - b: the y intercept of the fitted line in cells (not meters)
	def LinearFit(self, full_scan, points, pose):
		using_adjacent = False
		if len(points) == 1:
			# try to find adjacent points
			for neighbor in full_scan:
				if (abs(neighbor[0] - points[0][0]) < self.disc * 2.0 and 
					abs(neighbor[1] - points[0][1]) < self.disc * 2.0 and
					neighbor[0] != points[0][0] and neighbor[1] != points[0][1]):
					using_adjacent = True
					points.append(neighbor)

		if len(points) == 1:
			# get perpendicular fit
			A = -1.0 * (points[0][0] - pose[0][2]) / (points[0][1] - pose[1][2])
			b = points[0][1] - (A * points[0][0])

		else:
			# get fit from orthogonal regression (thanks scipy!)
			points_arr = np.array(points)
			data = scipy.odr.RealData(points_arr[:,0],points_arr[:,1])
			odr = scipy.odr.ODR(data, scipy.odr.polynomial(1))
			output = odr.run()
			b = output.beta[0]
			A = output.beta[1]

		b /= self.disc

		if using_adjacent:
			while len(points) > 1.0:
				del points[len(points)-1]

		return A,b

	# groups scan endpoints that fall into the same map cell
	# params:
	# - points:       list of scan endpoints expressed in the global frame
	# returns:
	# - point groups: list of lists of points, each list of points falls into the
	#                 same map cell
	def GroupPointsByCell(self, points):

		# iterate over all scan endpoints, finding groups that occupy the same map cell
		point_groups = []
		grouped = [False] * len(points)

		for point_idx in range(len(points)):

			cur_point = points[point_idx]

			x_min,x_max,y_min,y_max = self.GetBoundingVertices(cur_point)

			if not grouped[point_idx]:
				
				cur_group = [cur_point]
				grouped[point_idx] = True
				
				for next_idx in range(point_idx + 1, len(points)):
					if not grouped[next_idx]:
						next_point = points[next_idx]
						x,y = self.PointToMapCoordinates(next_point)
						if (x >= x_min and x < x_max
							and y >= y_min and y < y_max):
							cur_group.append(next_point)
							grouped[next_idx] = True

				point_groups.append(cur_group)

		return point_groups


	# gets the map vertices on either side of the given point
	# params:
	# - point: point in the global frame
	# returns:
	# - x_min: map vertex index immediately below the given point in x
	# - x_max: map vertex index immediately above the given point in x
	# - y_min: map vertex index immediately below the given point in y
	# - y_max: map vertex index immediately above the given point in y
	def GetBoundingVertices(self, point):

		# find four grid vertices that bound the current point
		x,y = self.PointToMapCoordinates(point)
		x_min = math.floor(x)
		x_max = x_min + 1.0
		y_min = math.floor(y)
		y_max = y_min + 1.0
		return x_min,x_max,y_min,y_max


	def PointToMapCoordinates(self, point):
		x = (point[0] / self.disc)
		y = (point[1] / self.disc)
		return x,y

	def MapCoordinatesToPoint(self, x, y):
		point = np.zeros(2)
		point[0] = x * self.disc
		point[1] = y * self.disc
		return point

	def GetMapValue(self, x, y):
		self.ExpandMap(x,y)
		return(self.map[int(x+self.offsets[0]),int(y+self.offsets[1])])

	def SetMapValue(self, x, y, val):
		self.ExpandMap(x,y)
		self.map[int(x+self.offsets[0]),int(y+self.offsets[1])] = val

	def GetPriority(self, x, y):
		self.ExpandMap(x,y)
		return(self.priorities[int(x+self.offsets[0]),int(y+self.offsets[1])])

	def SetPriority(self, x, y, val):
		self.ExpandMap(x,y)
		self.priorities[int(x+self.offsets[0]),int(y+self.offsets[1])] = val


	# returns the value at a particular point in the map as well as
	# the gradient at that point, interpolated from the discretized
	# values in the map
	# params: 
	# - point: 1x2 numpy array, a point in the global frame
	# returns:
	# - value: float, interpolated map value at the given point
	# - grad:  1x2 numpy array, interpolated map gradients at the given point
	def GetMapValueAndGradient(self, point):

		# get point in map coordinates
		point_x,point_y = self.PointToMapCoordinates(point)
		d = np.array([point_x,point_y])

		# get bounding vertices of the given point
		x_min,x_max,y_min,y_max = self.GetBoundingVertices(point)

		tx = point_x - x_min
		ty = point_y - y_min

		# get map values at the bounding vertices
		m_points = []
		m_points.append((int(x_min), int(y_min)))
		m_points.append((int(x_max), int(y_min)))
		m_points.append((int(x_max), int(y_max)))
		m_points.append((int(x_min), int(y_max)))

		m = np.zeros(4)
		for i in range(4):
			m[i] = self.GetMapValue(m_points[i][0],m_points[i][1])

		# get number of sign changes between adjacent cells
		sign_changes = 0
		neg_count = 0
		pos_count = 0

		# count number of sign changes and number of negatives and positives
		for cell_idx in range(4):
			cur_sign = np.sign(m[cell_idx])
			if cur_sign == 0.0: cur_sign = 1.0
			last_sign = np.sign(m[cell_idx-1])
			if last_sign == 0.0: last_sign = 1.0
			if cur_sign == -1.0:
				neg_count += 1
			else:
				pos_count += 1
			if cur_sign != last_sign:
				sign_changes += 1

		grad = np.zeros(2)
		
		value = ty*(m[2]*tx + m[3]*(1-tx)) + (1-ty)*(m[1]*tx + m[0]*(1-tx))
		sign = np.sign(value)
		value = abs(value)
		
		if sign_changes != 2:
			grad[0] = ty * (m[3] - m[2]) + (1.0 - ty) * (m[1] - m[0])
			grad[1] = tx * (m[1] - m[2]) + (1.0 - tx) * (m[0] - m[3])
			grad = grad * sign

			if math.isnan(value):
				print("no sign change")
		else:
			num_pts = 4

			# allocate cells to pairs
			pairs = [[0,0],[0,0]]
			if neg_count == 2: 
				neg_idx = 0
				pos_idx = 0
				for m_idx in range(num_pts):
					if np.sign(m[m_idx]) < 0:
						pairs[neg_idx][1] = m_idx
						neg_idx += 1
					else:
						pairs[pos_idx][0] = m_idx
						pos_idx += 1
				# check to make sure paired points are adjacent
				if abs(pairs[0][0] - pairs[0][1]) != 1:
					temp = pairs[0][1]
					pairs[0][1] = pairs[1][1]
					pairs[1][1] = temp
			elif neg_count == 1: 
				for m_idx in range(num_pts):
					if np.sign(m[m_idx]) < 0:
						pairs[0][1] = m_idx
						pairs[1][1] = m_idx
						pairs[0][0] = (m_idx + 1) % num_pts
						pairs[1][0] = (m_idx - 1) % num_pts
			else:
				for m_idx in range(num_pts):
					if np.sign(m[m_idx]) >= 0:
						pairs[0][0] = m_idx
						pairs[1][0] = m_idx
						pairs[0][1] = (m_idx + 1) % num_pts
						pairs[1][1] = (m_idx - 1) % num_pts


			
			# separate values into pos/neg pairs and calculate p0 and p1,
			# two points that define g(r), the zero line running between
			# the four points
			p = np.zeros((2,2))
			#print(pairs)
			m_x_plus = np.zeros(2)
			m_y_plus = np.zeros(2)
			m_x_minus = np.zeros(2)
			m_y_minus = np.zeros(2)
			m_plus = np.zeros(2)
			m_minus = np.zeros(2)

			for pair_idx in range(2):
				m_x_plus[pair_idx] = m_points[pairs[pair_idx][0]][0]
				m_y_plus[pair_idx] = m_points[pairs[pair_idx][0]][1]
				m_x_minus[pair_idx] = m_points[pairs[pair_idx][1]][0]
				m_y_minus[pair_idx] = m_points[pairs[pair_idx][1]][1]
				m_plus[pair_idx] = m[pairs[pair_idx][0]]
				m_minus[pair_idx] = m[pairs[pair_idx][1]]
				#print(str(m_plus) + ',' + str(m_minus))
				if m_plus[pair_idx] - m_minus[pair_idx] == 0:
					print('zero encountered, m+: {:f},  m-: {:f}'.format(m_plus[pair_idx],m_minus[pair_idx]))
					print('num neg: {:d}   sign changes: {:d}\n'.format(neg_count,sign_changes))
				p[pair_idx,0] = m_x_plus[pair_idx]+(m_plus[pair_idx]/(m_plus[pair_idx]-m_minus[pair_idx]))*(m_x_minus[pair_idx]-m_x_plus[pair_idx])
				p[pair_idx,1] = m_y_plus[pair_idx]+(m_plus[pair_idx]/(m_plus[pair_idx]-m_minus[pair_idx]))*(m_y_minus[pair_idx]-m_y_plus[pair_idx])

			#print(p)
			'''
			v = np.array([p[1,1] - p[0,1], -p[1,0] + p[0,0]])
			if np.linalg.norm(v) > 0.0:
				v = v / np.linalg.norm(v)

			dist = abs((p[1,0]-p[0,0])*(p[0,1]-d[1]) - (p[0,0]-d[0])*(p[1,1]-p[0,1]))/math.sqrt((p[1,0]-p[0,0])**2 + (p[1,1]-p[0,1])**2)

			if dist > 0.0:
				grad = v * value / dist
			else:
				grad = np.array([0,0])
			'''
			# calculate q, the projection of the scan endpoint onto g(r)
			'''
			q = p[0,:] + ((d-p[0,:])*(p[1,:]-p[0,:])/((p[1,:]-p[0,:])**2)) * (p[1,:] - p[0,:])

			for i in range(2):
				if math.isnan(q[i]):
					q[i] = p[0,i]
			grad = q-d
			#value = np.linalg.norm(grad)
			'''
			
			A = np.array([[p[1,0]-p[0,0], p[1,1]-p[0,1]],
						  [p[0,1]-p[1,1], p[1,0]-p[0,0]]])

			if np.linalg.matrix_rank(A) < 2:
				print("singular matrix: \n{:s}".format(A))
				print("matrix determinant: {:f}".format(np.linalg.det(A)))
				print("\nx: {:f}   y: {:f}   residual: {:f}".format(d[0],d[1],value))
				print("p0: {:s}   p1: {:s}".format(p[0,:],p[1,:]))
				print("m+: {:f},{:f}: {:f}   m-: {:f},{:f}: {:f}".format(m_x_plus[0],m_y_plus[0],m_plus[0],m_x_minus[0],m_y_minus[0],m_minus[0]))
				print("m+: {:f},{:f}: {:f}   m-: {:f},{:f}: {:f}".format(m_x_plus[1],m_y_plus[1],m_plus[1],m_x_minus[1],m_y_minus[1],m_minus[1]))
				print("gradient: {:s}".format(grad))
				print("distance: {:f}".format(value))
				#print("q: {:s}".format(q))
				print("d: {:s}".format(d))
				print("tx: {:f}   ty: {:f}".format(tx,ty))

			b = np.array([[-d[0]*(p[1,0]-p[0,0]) - d[1]*(p[1,1]-p[0,1])],
						  [-p[0,1]*(p[1,0]-p[0,0]) + p[0,0]*(p[1,1]-p[0,1])]])
			q = np.dot(np.linalg.inv(A), -1.0*b)
			q = np.squeeze(q)
			grad = q - d
			#value = np.linalg.norm(grad)
				
		if np.linalg.norm(grad) > 0:
			grad = grad / np.linalg.norm(grad)
		return value,grad

