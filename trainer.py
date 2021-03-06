import tensorflow as tf
import model
import matcher
from matcher import Matcher
import svt_data_loader as sLoader
import constants as c
from constants import layer_boxes, classes
from tb_common import *
import numpy as np
import signal
import sys
import cv2
import colorsys
import time

flags = tf.app.flags
FLAGS = flags.FLAGS

class TB:
	def __init__(self, model_dir=None):
		self.sess = tf.Session()
		
		self.imgs_ph, self.bn, self.output_tensors, self.pred_labels, self.pred_locs = model.model(self.sess)

		total_boxes = self.pred_labels.get_shape().as_list()[1]
		self.positives_ph, self.negatives_ph, self.true_labels_ph, self.true_locs_ph, self.total_loss, self.class_loss, self.loc_loss = \
			model.loss(self.pred_labels, self.pred_locs, total_boxes)

		out_shapes = [out.get_shape().as_list() for out in self.output_tensors]

		c.out_shapes = out_shapes
		
		c.defaults = model.default_boxes(out_shapes)
		# variables in model are already initialized, so only initialize those declared after
		with tf.variable_scope("optimizer"):
			self.global_step = tf.Variable(0)
			self.lr_ph = tf.placeholder(tf.float32)
			self.optimizer = tf.train.AdamOptimizer(1e-3).minimize(self.total_loss, global_step=self.global_step)
		new_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope="optimizer")
		init = tf.variables_initializer(new_vars)
		self.sess.run(init)

		if model_dir is None:
			model_dir = FLAGS.model_dir

		ckpt = tf.train.get_checkpoint_state(model_dir)
		self.saver = tf.train.Saver()

		if ckpt and ckpt.model_checkpoint_path:
			self.saver.restore(self.sess, ckpt.model_checkpoint_path)
			print("restored %s" % ckpt.model_checkpoint_path)

	def single_image(self, sample, min_conf=0.01, nms=0.45):
		resized_img = cv2.resize(sample, (image_size, image_size))
		pred_labels_f, pred_locs_f, step = self.sess.run([self.pred_labels, self.pred_locs, self.global_step], feed_dict={self.imgs_ph: [resized_img], self.bn: False})
		boxes_, confidences_ = matcher.format_output(pred_labels_f[0], pred_locs_f[0])
		resize_boxes(resized_img, sample, boxes_, scale=float(image_size))

		return postprocess_boxes(boxes_, confidences_, min_conf, nms)

def default2cornerbox(default, offsets):
	c_x = default[0] + offsets[0] * default[2]
	c_y = default[1] + offsets[1] * default[3]
	w = np.exp(offsets[2])
	h = np.exp(offsets[3])

	return [c_x - w/2.0, c_y - h/2.0, w, h]

def calc_offsets(default, truth):
	dX = (truth[0] - default[0]) / float(default[2])
	dY = (truth[1] - default[1]) / float(default[3])
	dW = np.log(truth[2] / default[2])
	dH = np.log(truth[3] / default[3])
	return [dX, dY, dW, dH]

def prepare_feed(matches):
	positives_list = []
	negatives_list = []
	true_labels_list = []
	true_locs_list = []

	for o in range(len(layer_boxes)):
		for x in range(c.out_shapes[o][2]):
			for y in range(c.out_shapes[o][1]):
				for i in range(layer_boxes[o]):
					match = matches[o][x][y][i]

					if isinstance(match, tuple): # there is a ground truth assigned to this default box
						positives_list.append(1)
						negatives_list.append(0)
						true_labels_list.append(match[1]) #id
						default = c.defaults[o][x][y][i]
						true_locs_list.append(calc_offsets(default, corner2centerbox(match[0])))
					elif match == -1: # this default box was chosen to be a negative
						positives_list.append(0)
						negatives_list.append(1)
						true_labels_list.append(classes) # background class
						true_locs_list.append([0]*4)
					else: # no influence for this training step
						positives_list.append(0)
						negatives_list.append(0)
						true_labels_list.append(classes)  # background class
						true_locs_list.append([0]*4)

	a_positives = np.asarray(positives_list)
	a_negatives = np.asarray(negatives_list)
	a_true_labels = np.asarray(true_labels_list)
	a_true_locs = np.asarray(true_locs_list)

	return a_positives, a_negatives, a_true_labels, a_true_locs


def draw_matches(I, boxes, matches, anns):
	I = np.copy(I) * 255.0

	for o in range(len(layer_boxes)):
		for x in range(c.out_shapes[o][2]):
			for y in range(c.out_shapes[o][1]):
				for i in range(layer_boxes[o]):
					match = matches[o][x][y][i]

					# None if not positive nor negative
					# -1 if negative
					# ground truth indices if positive

					if match == -1:
						coords = center2cornerbox(boxes[o][x][y][i])
						draw_rect(I, coords, (255, 0, 0))
					elif isinstance(match, tuple):
						coords = center2cornerbox(boxes[o][x][y][i])
						draw_rect(I, coords, (0, 0, 255))
						# elif s == 2:
						#    draw_rect(I, boxes[o][x][y][i], (0, 0, 255), 2)

	for gt_box in anns:
		draw_rect(I, gt_box, (0, 255, 0), 3)

	I = cv2.cvtColor(I.astype(np.uint8), cv2.COLOR_RGB2BGR)
	cv2.imshow("matches", I)
	cv2.waitKey(1)

def draw_matches2(I, pos, neg, true_labels, true_locs):
	I = np.copy(I) * 255.0
	index = 0

	for o in range(len(layer_boxes)):
		for x in range(c.out_shapes[o][2]):
			for y in range(c.out_shapes[o][1]):
				for i in range(layer_boxes[o]):
					if pos[index] > 0:
						d = c.defaults[o][x][y][i]
						coords = default2cornerbox(d, true_locs[index])
						draw_rect(I, coords, (0, 255, 0))
						coords = center2cornerbox(d)
						draw_rect(I, coords, (0, 0, 255))
					elif neg[index] > 0:
						pass
						#d = defaults[o][x][y][i]
						#coords = default2global(d, pred_locs[index])
						#draw_rect(I, coords, (255, 0, 0))
						#cv2.putText(I, coco.i2name[true_labels[index]],
						#            (int(coords[0] * image_size), int((coords[1] + coords[3]) * image_size)),
						#            cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0))

					index += 1

	I = cv2.cvtColor(I.astype(np.uint8), cv2.COLOR_RGB2BGR)
	cv2.imshow("matches2", I)
	cv2.waitKey(1)

def basic_nms(boxes, thres=0.45):
	re = []

	def pass_nms(c, lab):
		for box_, conf_, top_label_ in re:
			if lab == top_label_ and calc_jaccard(c, box_) > thres:
				return False
		return True

	index = 0

	for box, conf, top_label in boxes:
		if top_label != classes and pass_nms(box, top_label):
			re.append((box, conf, top_label))
			#re.append(index)

			if len(re) >= 200:
				break
		index += 1

	return re

def postprocess_boxes(boxes, confidences, min_conf=0.01, nms=0.45):
	filtered = []

	for box, conf, top_label in confidences:
		if conf >= min_conf:
			coords = boxes[box[0]][box[1]][box[2]][box[3]]
			coords = center2cornerbox(coords)

			filtered.append((coords, conf, top_label))

	return basic_nms(filtered, nms)


def draw_outputs(img, boxes, confidences, wait=1):
	I = img * 255.0

	#nms = non_max_suppression_fast(np.asarray(filtered_boxes), 1.00)
	picks = postprocess_boxes(boxes, confidences)

	for box, conf, top_label in picks:#[filtered[i] for i in picks]:
		if top_label != classes:
			#print("%f: %s %s" % (conf, coco.i2name[top_label], box))

			c = colorsys.hsv_to_rgb(((top_label * 17) % 255) / 255.0, 1.0, 1.0)
			c = tuple([255*c[i] for i in range(3)])

	I = cv2.cvtColor(I.astype(np.uint8), cv2.COLOR_RGB2BGR)
	cv2.imshow("outputs", I)
	cv2.waitKey(wait)

def start_train():
	
	tb = TB()

	t = time.time()

	def signal_handler(signal, frame):
		print('You pressed Ctrl+C!')
		tb.saver.save(tb.sess, "%s/ckpt" % FLAGS.model_dir, step)
		sys.exit(0)

	signal.signal(signal.SIGINT, signal_handler)

	# summary_writer = tf.train.SummaryWriter(FLAGS.model_dir)
	box_matcher = Matcher()

	train_loader = sLoader.SVT('./svt1/train.xml', './svt1/test.xml')

	while True:

		imgs, anns = train_loader.nextBatch(FLAGS.batch_size)

		pred_labels_f, pred_locs_f, step = tb.sess.run([tb.pred_labels, tb.pred_locs, tb.global_step],
														feed_dict={tb.imgs_ph: imgs, tb.bn: False})

		batch_values = [None for i in range(FLAGS.batch_size)]

		def match_boxes(batch_i):
			#a = time.time()
			matches = box_matcher.match_boxes(pred_labels_f[batch_i], anns[batch_i])
			#print("a: %f" % (time.time() - a))
			#a = time.time()
			positives_f, negatives_f, true_labels_f, true_locs_f = prepare_feed(matches)

			batch_values[batch_i] = (positives_f, negatives_f, true_labels_f, true_locs_f)

			if batch_i == 0:
				boxes_, confidences_ = matcher.format_output(pred_labels_f[batch_i], pred_locs_f[batch_i])
				if FLAGS.display:
					draw_outputs(imgs[batch_i], boxes_, confidences_)
					draw_matches(imgs[batch_i], c.defaults, matches, anns[batch_i])
					draw_matches2(imgs[batch_i], positives_f, negatives_f, true_labels_f, true_locs_f)
			#print("b: %f" % (time.time() - a))

		for batch_i in range(FLAGS.batch_size):
			match_boxes(batch_i)

		positives_f, negatives_f, true_labels_f, true_locs_f = [np.stack(m) for m in zip(*batch_values)]

		if step < 4000:
			lr = 8e-4
		elif step < 180000:
			lr = 1e-3
		elif step < 240000:
			lr = 1e-4
		else:
			lr = 1e-5

		_, c_loss_f, l_loss_f, loss_f, step = tb.sess.run([tb.optimizer, tb.class_loss, tb.loc_loss, tb.total_loss, tb.global_step],
								   feed_dict={tb.imgs_ph: imgs, tb.bn: True, tb.positives_ph:positives_f, tb.negatives_ph:negatives_f,
										   tb.true_labels_ph:true_labels_f, tb.true_locs_ph:true_locs_f, tb.lr_ph:lr})

		t = time.time() - t
		print("%i: %f (%f secs)" % (step, loss_f, t))
		t = time.time()

		# tfc.summary_float(step, "loss", loss_f, summary_writer)
		# tfc.summary_float(step, "class loss", c_loss_f, summary_writer)
		# tfc.summary_float(step, "loc loss", l_loss_f, summary_writer)

		if step % 1000 == 0:
			tb.saver.save(tb.sess, "%s/ckpt" % FLAGS.model_dir, step)

def evaluate_images():
	tb = TB()

	cv2.namedWindow("outputs", cv2.WINDOW_NORMAL)
	test_loader = sLoader.SVT('./svt1/train.xml', './svt1/test.xml')

	while True:
		imgs, anns = test_loader.nextBatch(3,'test')
		pred_labels_f, pred_locs_f, step = tb.sess.run([tb.pred_labels, tb.pred_locs, tb.global_step],
														feed_dict={tb.imgs_ph: imgs, tb.bn: False})
		boxes_, confidences_ = matcher.format_output(pred_labels_f[0], pred_locs_f[0])
		draw_outputs(imgs[0], boxes_, confidences_, wait=0)

def resize_boxes(resized, original, boxes, scale=1.0):
	scale_x = original.shape[1] / float(resized.shape[1]) * scale
	scale_y = original.shape[0] / float(resized.shape[0]) * scale

	for o in range(len(layer_boxes)):
		for y in range(c.out_shapes[o][2]):
			for x in range(c.out_shapes[o][1]):
				for i in range(layer_boxes[o]):
					boxes[o][x][y][i][0] *= scale_x
					boxes[o][x][y][i][1] *= scale_y
					boxes[o][x][y][i][2] *= scale_x
					boxes[o][x][y][i][3] *= scale_y

def get_image_detections(path):
	tb = TB()

	#cv2.namedWindow("outputs", cv2.WINDOW_NORMAL)
	sample = cv2.imread(path)[:, :, :3]
	sample = cv2.resize(sample, (300,300))

	boxes_, confidences_ = tb.single_image(sample)

	return boxes_, confidences_

def evaluate_image(path):
	boxes_, confidences_ = get_image_detections(path)

	sample = cv2.imread(path)
	sample = cv2.resize(sample, (300,300))
	#resized_img = skimage.transform.resize(sample, (image_size, image_size))

	#draw_outputs(resized_img, boxes_, confidences_, wait=0)

	draw_outputs(np.asarray(sample) / 255.0, boxes_, confidences_, wait=0)

if __name__ == "__main__":
	flags.DEFINE_string("model_dir", "summaries/train0", "model directory")
	flags.DEFINE_integer("batch_size", 8, "batch size")
	flags.DEFINE_boolean("display", True, "display relevant windows")
	flags.DEFINE_string("mode", "train", "train, images, image, webcam")
	flags.DEFINE_string("image_path", "", "path to image")
	flags.DEFINE_string("webcam_ip", "", "webcam ip")

	if FLAGS.mode == "train":
		start_train()
	elif FLAGS.mode == "images":
		evaluate_images()
	elif FLAGS.mode == "image":
		evaluate_image(FLAGS.image_path)
