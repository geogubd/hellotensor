from cv2tools import filt,vis
import os
import cv2
import numpy as np
import math

def rn(i):
    return np.random.uniform() * i

source = 'D:/DroneSamples/'

transparent_dir = './drones_transparent/'
drones_resized = './drones_resized/'

bg_dir = './bg_raw/'
bg_resized = './bg_resized/'

def get_file_names(path):
    files = os.listdir(path)
    onlyfiles = [os.path.join(path,f) for f in files if os.path.isfile(os.path.join(path, f))]
    return onlyfiles

print('scanning...')
drone_images = get_file_names(source+drones_resized)
bg_images = get_file_names(source+bg_resized)
print('got',len(drone_images),'drones +',len(bg_images),'backgrounds')

# image reader
def imread(path):
    return cv2.imread(path,cv2.IMREAD_UNCHANGED).astype('float32')/255.
    # normalize

# now load em into memory
print('loading into memory...')
drone_images = [imread(path) for path in drone_images]
bg_images = [imread(path) for path in bg_images]
print('loaded.')
print(drone_images[0].shape)
print(bg_images[0].shape)

# class to hold a foreground-background pair
class ForeBackPair:
    def __init__(self,fg,bg):
        self.fg = fg
        self.bg = bg
        self.fg_scale = 1.
        self.fg_angle = 0.
        self.bg_scale = 1.

        # find a starting point on background
        self.bg_offsets = rn(bg.shape[0]-200)+100,rn(bg.shape[1]-200)+100

    # note: all (y,x) or (h,w) pairs!
    def compose_one(self,output_size,fg_offsets,bg_offsets,fg_blur=[0,0],bg_blur=[0,0]):
        def limit(lower,upper): # generate a limiter
            return lambda x:int(np.clip(x,a_min=lower,a_max=upper))
        # 1. crop a piece from background
        oh,ow = output_size
        oh,ow = oh*self.bg_scale, ow*self.bg_scale

        # bg params
        bgh,bgw,_ = self.bg.shape
        bgoy, bgox = self.bg_offsets
        bgoy, bgox = bgoy+bg_offsets[0], bgox+bg_offsets[1] # apply input offset

        # obtain intersection of intended crop area and valid area
        isect = filt.intersect([0,0],[int(bgox-ow/2), int(bgoy-oh/2)],[bgh,bgw],[int(oh),int(ow)])
        if isect==False:
            raise NameError('maybe too close to border.')

        tl,br,sz = isect
        # print('isect',isect)
        bgcrop = self.bg[tl[0]:br[0],tl[1]:br[1]]
        # print('cropped shape:',bgcrop.shape,'resize into:',output_size)

        # 2. resize that bg crop into output_size
        bgcrop = cv2.resize(bgcrop,dsize=(output_size[1],output_size[0]),
            interpolation=cv2.INTER_LINEAR) # use bilinear
        # print('bgcrop resized. shape:',bgcrop.shape)

        # 3. mblur the bg
        bgcrop = filt.apply_vector_motion_blur(bgcrop,bg_blur)

        # 3. motion blur the foreground, before rotation and scale
        rad = np.arctan2(-fg_blur[0],fg_blur[1])
        l1 = max(abs(fg_blur[0]),abs(fg_blur[1])) * (1./self.fg_scale)
        l1 = int(l1)
        fgblurred = filt.apply_motion_blur(self.fg,
            dim=l1,
            angle=(rad/math.pi*180)-self.fg_angle)

        # 4. rotate and scale the foreground
        fgscaled = filt.rotate_scale(fgblurred,
            self.fg_angle,
            self.fg_scale)
        # print(self.fg.shape,'fg scaled. shape:',fgscaled.shape)
        # vis.show_autoscaled(fgscaled)

        # # 4. motion blur the foreground
        # fgscaled = filt.apply_vector_motion_blur(fgscaled,fg_blur)

        # 5. blur the alpha a little bit
        fgscaled[:,:,3] = cv2.blur(fgscaled[:,:,3:4],(2,2))

        # 6. alpha overlay with offsets
        offsets = [output_size[0]//2-fgscaled.shape[0]//2+fg_offsets[0], \
                    output_size[1]//2-fgscaled.shape[1]//2+fg_offsets[1]]
        offsets = [int(k) for k in offsets]
        bgcorp = filt.alpha_composite(bgcrop,fgscaled,offsets)

        out = bgcrop

        # 7. ground truth label
        offsets = [output_size[0]//2-fgscaled.shape[0]//2+fg_offsets[0], \
                    output_size[1]//2-fgscaled.shape[1]//2+fg_offsets[1]]
        offsets = [int(k) for k in offsets]
        gt = np.zeros(output_size+[1],dtype='float32')
        isectgr = filt.intersect_get_roi(gt,fgscaled[:,:,3:4],offsets)
        if isectgr is None:
            pass
        else:
            fgroi,bgroi = isectgr
            bgroi[:] = fgroi[:]
            # bgroi[:] = 1.

        return out,gt

    def compose_batch(self,output_size,batch_size,show=True):
        bs = batch_size
        bgrw,bgs = random_walk(bs,ipos=.5,ispd=2.,acc=0.2) #rw position and speed
        fgrw,fgs = random_walk(bs,ipos=.5,ispd=5.,acc=2.)

        bgrw -= np.mean(bgrw) # normalization
        fgrw -= np.mean(fgrw)+np.random.uniform(40)-20 # normalization

        batch_img = []
        batch_gt = []

        for i in range(len(bgrw)):
            if True: # test flag
                img,gt = self.compose_one(output_size,fgrw[i],bgrw[i],
                    fg_blur=fgs[i]/2,bg_blur=bgs[i]/2)
            else:
                img,gt = self.compose_one(output_size,fgrw[i]*0,bgrw[i]*0,
                    fg_blur=fgs[i]/2*0,bg_blur=bgs[i]/2*0)

            batch_img.append(img)
            batch_gt.append(gt)

        if show==True:
            for k in range(len(batch_img)):
                vis.show_autoscaled(batch_img[k],name='img',limit=130.)
                vis.show_autoscaled(batch_gt[k],name='gt',limit=130.)

        # both generated and ground truth
        return np.stack(batch_img,axis=0),np.stack(batch_gt,axis=0)

def random_walk(length,ipos,ispd,acc):
    # generation length, initial position variance, initial speed variance, acceleration variance
    bs = length
    # 1. initial position (y,x) vectors
    position = np.random.normal(loc=0.,scale=ipos,size=(1,2))
    position[:,0]
    position[:,1]

    # 2. initial speed
    ispeed = np.random.normal(loc=0., scale=ispd, size=(1,2))
    # 3. generate acceleration (y,x) vectors
    acceleration = np.random.normal(loc=0., scale=acc, size=(bs,2))
    # 4. intergrate acc to speed
    speed = np.cumsum(acceleration, axis=0) + ispeed
    # 5. intergrate speed to position
    positions = np.cumsum(speed, axis=0) + position

    return positions,speed

def compose_one_batch(bs,random=False,show=True):
    while True:
        try:
            if random:
                drone_id = np.random.choice(len(drone_images))
                bg_id = np.random.choice(len(bg_images))
            else:
                drone_id,bg_id = 0,0

            fbp = ForeBackPair(drone_images[drone_id],bg_images[bg_id])
            fbp.fg_scale = rn(1)**2*0.4 + 0.2
            fbp.bg_scale = 0.9 + rn(3)
            fbp.fg_angle = rn(180)-90
            bimg,bgt = fbp.compose_batch([96,96], bs, show=show)
            break
        except NameError as e:
            print(e)
            print('failed (might due to exceeding bg image). retry...')
        except:
            raise
    return bimg,bgt

def test():
    compose_one_batch(20)

def generate():
    num_track = 2000 # number of tracks
    num_per_track = 8 # length of each track

    global timg,tgt
    timg,tgt = [],[]
    print('total:',num_track,'per track:',num_per_track)
    for i in range(num_track):
        print('track #',i)
        bimg,bgt = compose_one_batch(num_per_track,random=True,show=(i%100==0))
        timg.append(bimg)
        tgt.append(bgt)

    timg = np.stack(timg,axis=0)
    tgt = np.stack(tgt,axis=0)

    timg = (timg*255.).astype('uint8')
    tgt = tgt>0.5

    print('generated.')
    print(timg.shape,tgt.shape)

def saveall(filename):
    with open(filename+'.npy','wb') as f:
        np.savez(f,timg=timg,tgt=tgt)