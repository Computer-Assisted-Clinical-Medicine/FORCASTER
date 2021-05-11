import numpy as np
import SimpleITK as sitk
#import forcast
import cal
import utils
#import i0
import os
import pydicom
import struct
#import scipy.interpolate
import astra
import time
import itertools

def write_vectors(name, vecs):
    with open(name+".csv", "w") as f:
        f.writelines([",".join([str(v) for v in vec])+"\n" for vec in vecs])

def read_vectors(path):
    with open(path, "r") as f:
        res = np.array([[float(v) for v in l.split(',')] for l in f.readlines().split()])
    return res

def smooth(vecs):
    res = np.zeros_like(vecs)
    from scipy.signal import savgol_filter
    for i in range(vecs.shape[1]):
        res[:,i] = savgol_filter(vecs[:,i], 11, 3) # window size 51, polynomial order 3
    return res

def normalize(images, mAs_array, kV_array, percent_gain):
    print("normalize images")
    kVs = {}
    kVs[40] = (20.4125, 677.964)
    kVs[50] = (61.6163, 686.4824)
    kVs[60] = (138.4021, 684.1844)
    kVs[70] = (250.8008, 691.9573)
    kVs[80] = (398.963, 701.1038)
    kVs[90] = (586.5949, 711.416)
    kVs[100] = (794.5124, 729.8813)
    kVs[109] = (1006.1, 750.0054)
    kVs[120] = (1252.2, 791.9865)
    kVs[125] = (1404.2202, 796.101)

    #f, gamma = i0.get_i0(prefix + r"\70kVp")
    #kVs[70] = f

    fs = []
    gain = 3
    #gain = 1
    for mAs, kV in zip(mAs_array, kV_array):
        if kV in kVs:
            f = np.polyval(kVs[kV], mAs)
        else:
            kVs_keys = np.array(list(sorted(kVs.keys())))
            if kV < kVs_keys[0]:
                f = np.polyval(kVs[kVs_keys[0]], mAs)
            elif kV > kVs_keys[-1]:
                f = np.polyval(kVs[kVs_keys[-1]], mAs)
            else:
                i1, i2 = np.argsort(np.abs(kVs_keys-kV))[:2]
                f1 = np.polyval(kVs[kVs_keys[i1]], mAs)
                f2 = np.polyval(kVs[kVs_keys[i2]], mAs)
                d1 = np.abs(kVs_keys[i1]-kV)*1.0
                d2 = np.abs(kVs_keys[i2]-kV)*1.0
                f = f1*(1.0-(d1/(d1+d2))) + f2*(1.0-(d2/(d1+d2)))
        fs.append(f)
    
    fs = np.array(fs).flatten()

    skip = 4
    if images.shape[1] < 1000:
        skip = 2
    #if images.shape[1] < 600:
    #    skip = 1

    edges = 1

    sel = np.zeros(images.shape, dtype=bool)
    #sel[:,20*skip:-20*skip:skip,20*skip:-20*skip:skip] = True
    #norm_images_gained = np.zeros((images.shape[0],images[0,20*skip:-20*skip:skip].shape[0], int(np.count_nonzero(sel)/images.shape[0]/(images[0,20*skip:-20*skip:skip].shape[0]))), dtype=np.float32)
    #norm_images_ungained = np.zeros((images.shape[0],images[0,20*skip:-20*skip:skip].shape[0], int(np.count_nonzero(sel)/images.shape[0]/(images[0,20*skip:-20*skip:skip].shape[0]))), dtype=np.float32)
    sel[:,edges:-edges:skip,edges:-edges:skip] = True
    sel_shape = (images.shape[0],images[0,edges:-edges:skip].shape[0], int(np.count_nonzero(sel)/images.shape[0]/(images[0,edges:-edges:skip].shape[0])))
    norm_images_gained = np.zeros(sel_shape, dtype=np.float32)
    norm_images_ungained = np.zeros(sel_shape, dtype=np.float32)
    
    gained_images = np.array([image*(1+gain/100) for image,gain in zip(images[sel].reshape(sel_shape), percent_gain)])
    
    for i in range(len(fs)):
        norm_img = gained_images[i].reshape(norm_images_gained[0].shape)
        norm_images_gained[i] = norm_img
        norm_img = images[i][sel[i]].reshape(norm_images_gained[0].shape)
        norm_images_ungained[i] = norm_img

    gain = 2.30
    #gain = 1
    offset = 400
    use = fs>1
    while (np.max(norm_images_gained, axis=(1,2))[use] > (gain*fs[use])).all():
        gain += 1
    #gain = np.ones_like(fs)
    #offset = 0
    for i in range(len(fs)):
        norm_img = norm_images_gained[i] / (offset + (gain*fs)[i])
        #norm_img = norm_images_gained[i] / (1.1*np.max(norm_images_gained[i]))
        norm_images_gained[i] = -np.log(norm_img)

    return norm_images_gained, norm_images_ungained, offset+gain*fs, fs

def read_dicoms(indir, max_ims=np.inf):
    print("read dicoms")
    kvs = []
    mas = []
    μas = []
    ts = []
    thetas = []
    phis = []
    prims = []
    secs = []
    ims = []
    percent_gain = []
    coord_systems = []
    sids = []
    sods = []

    #sid = []
    for root, _dirs, files in os.walk(indir):
        for entry in files:
            path = os.path.abspath(os.path.join(root, entry))
            #read DICOM files
            ds = pydicom.dcmread(path)
            if "PositionerPrimaryAngleIncrement" in dir(ds):
                ims = ds.pixel_array
                ts = list(range(len(ims)))
                thetas = np.array(ds.PositionerPrimaryAngleIncrement)
                phis = np.array(ds.PositionerSecondaryAngleIncrement)
                
                if ds[0x0021,0x1059].VR == "FL":
                    cs = np.array(ds[0x0021,0x1059].value).reshape((len(ts), 3, 4))
                    coord_systems = np.array(ds[0x0021,0x1059].value).reshape((len(ts), 3, 4))
                else:
                    cs = np.array(list(struct.iter_unpack("<f", ds[0x0021,0x1059].value))).reshape((len(ts), 3, 4))
                    coord_systems = np.array(list(struct.iter_unpack("<f", ds[0x0021,0x1059].value))).reshape((len(ts), 3, 4))
                
                if ds[0x0021,0x1031].VR == "SS":
                    sids = np.array(ds[0x0021,0x1031].value)*0.1
                else:
                    sids = np.array(list(struct.iter_unpack("<h", ds[0x0021,0x1031].value))).flatten()*0.1
                
                
                #if ds[0x0021,0x1017].VR == "SL":
                #    sods = [np.array(ds[0x0021,0x1017].value)]*len(ts)
                #else:
                #    sods = [np.array(int.from_bytes(ds[0x0021,0x1017].value, "little", signed=True))]*len(ts)
                
                stparmdata = utils.unpack_sh_stparm(ds[0x0021,0x1012].value)
                sods = [stparmdata["SOD_A"]] *len(ts)

                if ds[0x0019,0x1008].VR == "US":
                    percent_gain = [ds[0x0019,0x1008].value] * len(ts)
                else:
                    percent_gain = [float(int.from_bytes(ds[0x0019,0x1008].value, "little", signed=False))] * len(ts)

                #sid = ds[0x0021,0x1031].value
                if ds[0x0021,0x100f].VR =="SL":
                    xray_info = np.array(ds[0x0021,0x100F].value)
                else:
                    xray_info = np.array(list(struct.iter_unpack("<l",ds[0x0021,0x100f].value))).flatten()
                kvs = xray_info[0::4]
                mas = xray_info[1::4]*xray_info[2::4]*0.001
                μas = xray_info[2::4]*0.001

            elif "NumberOfFrames" in dir(ds):
                ims = ds.pixel_array
                for i in range(int(ds.NumberOfFrames)):
                    ts.append(len(ts))
                    kvs.append(float(ds.KVP))
                    mas.append(float(ds.XRayTubeCurrent)*float(ds.ExposureTime)*0.001)
                    if ds[0x0021,0x1004].VR == "SL":
                        μas.append(float(ds[0x0021,0x1004].value)*0.001)
                    else:
                        μas.append(struct.unpack("<l", ds[0x0021,0x1004].value)[0]*0.001)
                    thetas.append(float(ds.PositionerPrimaryAngle))
                    phis.append(float(ds.PositionerSecondaryAngle))

                    stparmdata = utils.unpack_sh_stparm(ds[0x0021,0x1012].value)

                    cs = np.array(stparmdata["COORD_SYS_C_ARM"]).reshape((3, 4))
                    cs[:,2] = utils.rotMat(i*360/ims.shape[0], cs[:,0]).dot(cs[:,2])
                    cs[:,1] = utils.rotMat(i*360/ims.shape[0], cs[:,0]).dot(cs[:,1])

                    coord_systems.append(cs)
                    sids.append(np.array(stparmdata["SID_A"]))
                    sods.append(np.array(stparmdata["SOD_A"]))

                    rv = cs[:,2]
                    rv /= np.sum(rv**2, axis=-1)
                    prim = np.arctan2(np.sqrt(rv[0]**2+rv[1]**2), rv[2])
                    prim = np.arccos(rv[2] / np.sqrt(rv[0]**2+rv[1]**2+rv[2]**2) )
                    prim = prim*180 / np.pi
                    

                    if cs[1,2]<0:
                        prim *= -1

                    if (prim > 50 and prim < 135) or (prim <-45 and prim > -135):
                        sec = np.arctan2(rv[1], rv[0])
                        sec = sec * 180 / np.pi
                        if cs[1,2]<0:
                            sec *= -1
                        sec -= 90
                    else:
                        sec = np.arctan2(rv[2], rv[0])
                        sec = sec * 180 / np.pi - 90

                    prims.append(prim)
                    secs.append(sec)
                    if ds[0x0019,0x1008].VR == "US":
                        percent_gain.append(ds[0x0019,0x1008].value)
                    else:
                        percent_gain.append(float(int.from_bytes(ds[0x0019,0x1008].value, "little", signed=False)))

            elif "PositionerPrimaryAngle" in dir(ds):
                ts.append(len(ts))
                kvs.append(float(ds.KVP))
                mas.append(float(ds.XRayTubeCurrent)*float(ds.ExposureTime)*0.001)
                if ds[0x0021,0x1004].VR == "SL":
                    μas.append(float(ds[0x0021,0x1004].value)*0.001)
                else:
                    μas.append(struct.unpack("<l", ds[0x0021,0x1004].value)[0]*0.001)
                thetas.append(float(ds.PositionerPrimaryAngle))
                phis.append(float(ds.PositionerSecondaryAngle))
                ims.append(ds.pixel_array)

                stparmdata = utils.unpack_sh_stparm(ds[0x0021,0x1012].value)

                cs = np.array(stparmdata["COORD_SYS_C_ARM"]).reshape((3, 4))

                coord_systems.append(cs)
                sids.append(np.array(stparmdata["SID_A"]))
                sods.append(np.array(stparmdata["SOD_A"]))

                rv = cs[:,2]
                rv /= np.sum(rv**2, axis=-1)
                prim = np.arctan2(np.sqrt(rv[0]**2+rv[1]**2), rv[2])
                prim = np.arccos(rv[2] / np.sqrt(rv[0]**2+rv[1]**2+rv[2]**2) )
                prim = prim*180 / np.pi

                if cs[1,2]<0:
                    prim *= -1

                if (prim > 50 and prim < 135) or (prim <-45 and prim > -135):
                    sec = np.arctan2(rv[1], rv[0])
                    sec = sec * 180 / np.pi
                    if cs[1,2]<0:
                        sec *= -1
                    sec -= 90
                else:
                    sec = np.arctan2(rv[2], rv[0])
                    sec = sec * 180 / np.pi - 90

                prims.append(prim)
                secs.append(sec)

                if ds[0x0019,0x1008].VR == "US":
                    percent_gain.append(ds[0x0019,0x1008].value)
                else:
                    percent_gain.append(float(int.from_bytes(ds[0x0019,0x1008].value, "little", signed=False)))
            
            del ds
            if len(ts)>=max_ims:
                break
        if len(ts)>=max_ims:
            break

    print("create numpy arrays")
    kvs = np.array(kvs)
    mas = np.array(mas)
    μas = np.array(μas)
    percent_gain = np.array(percent_gain)
    thetas = np.array(thetas)
    phis = np.array(phis)
    ims = np.array(ims)
    coord_systems = np.array(coord_systems)
    sids = np.array(sids)
    sods = np.array(sods)

    cs = coord_systems
    
    rv = cs[:,:,2]
    rv /= np.vstack((np.sum(rv, axis=-1),np.sum(rv, axis=-1),np.sum(rv, axis=-1))).T
    prim = np.arctan2(np.sqrt(rv[:,0]**2+rv[:,1]**2), rv[:,2])
    prim = np.arccos(rv[:,2] / np.sqrt(rv[:,0]**2+rv[:,1]**2+rv[:,2]**2) )
    sec = 0.5*np.pi-np.arctan2(rv[:,1], rv[:,0])
    prim = prim*180 / np.pi
    sec = sec * 180 / np.pi

    prim[cs[:,1,2]<0] *= -1
    sec[cs[:,1,2]<0] -= 180
    prim[np.bitwise_and(cs[:,2,2]<0, cs[:,1,1]>0) ] -= 180
    prim[cs[:,1,0]>0] -= 180

    #thetas = prim
    #phis = sec
    angles = np.vstack((thetas*np.pi/180.0, phis*np.pi/180.0, np.zeros_like(thetas))).T

    ims_gained, ims_ungained, i0s_gained, i0s_ungained = normalize(ims, μas, kvs, percent_gain)

    return ims_gained, ims_ungained, i0s_gained, i0s_ungained, angles, coord_systems, sids, sods

def reg_rough(ims, params, config, c=0):
    corrs = []
    noise = config["noise"]
    for i in range(len(params)):
    #for i in [29]:
        print(i, end=",", flush=True)
        cur = params[i]
        real_img = cal.Projection_Preprocessing(ims[i])
        config["real_img"] = real_img
        config["noise"] = (noise[0][i], noise[1][i])
        for si in range(1):
            proj_d = cal.Projection_Preprocessing(config["Ax"](np.array([cur])))[:,0]
            try:
                old_cur = np.array(cur)
                cur = cal.roughRegistration(cur, proj_d, config, c)
            except Exception as ex:
                print(i, ex, cur)
                raise
            #if (np.abs(old_cur-cur)<1e-8).all():
            #    print(si, end=" ", flush=True)
            #    break
        corrs.append(cur)
        print(flush=True)
        
    corrs = np.array(corrs)
    #print(corrs)
    return corrs

import multiprocessing as mp
import sys
import io

def it_func(con, Ax_params, ready):
    print("start")
    Ax = utils.Ax_param_asta(*Ax_params)
    while True:
        try:
            con.send(("ready",))
            ready.set()
            (i, cur, im, proj, noise, method) = con.recv()
            #print(i)
            old_stdout = sys.stdout
            sys.stdout = stringout = io.StringIO()
        except EOFError:
            break
        real_img = cal.Projection_Preprocessing(im)
        cur_config = {"real_img": real_img, "Ax": Ax, "noise": noise}
        try:
            cur = cal.roughRegistration(cur, proj, cur_config, method)
        except Exception as ex:
            print(ex, i, cur)
        #corrs.append(cur)
        stringout.flush()
        con.send(("result",i,cur,stringout.getvalue()))
        ready.set()
        stringout.close()
        sys.stdout = old_stdout

def reg_rough_parallel(ims, params, config, c=0):
    corrs = []
    pool_size = mp.cpu_count()
    pool = []
    cons = []
    #corrsq = mp.Queue()
    ready = mp.Event()
    for _ in range(pool_size):
        cons.append(mp.Pipe(True))
        pool.append(mp.Process(target=it_func, args=(cons[-1][1], config["Ax_gen"], ready)))
        pool[-1].start()

    proj_ds = cal.Projection_Preprocessing(config["Ax"](np.array(params)))
    
    corrs = [None]*len(params)
    for i in range(len(params)):
        ready_con = None
        while ready_con is None:
            ready.clear()
            for (con, _) in cons:
                if con.poll():
                    res = con.recv()
                    if res[0] == "ready":
                        ready_con = con
                        break
                    elif res[0] == "result":
                        corrs[res[1]] = res[2]
                        print(res[1], res[3])
            if ready_con is None:
                ready.wait(1)
        ready_con.send((i, params[i], ims[i], proj_ds[:,i], (config["noise"][0][i],config["noise"][1][i]), c))

    while len(cons) > 0:
        finished_con = []
        ready.clear()
        for con in cons:
            if con[0].poll():
                res = con[0].recv()
                if res[0] == "ready":
                    finished_con.append(con)
                elif res[0] == "result":
                    corrs[res[1]] = res[2]
                    print(res[1], res[3])
        for con in finished_con:
            cons.remove(con)
            con[0].close()
            con[1].close()
        if len(cons) > 0:
            ready.wait()

    for (con1, con2) in cons:
        con1.close()
        con2.close()
    corrs = np.array(corrs)
    #print(corrs)
    return corrs


def reg_lessrough(ims, params, Ax, feature_params):
    corrs = []
    for i in range(len(ims)):
        print(i, end=",", flush=True)
        cur = params[i]
        #print(cur)
        real_img = forcast.Projection_Preprocessing(ims[i])
        proj_d = forcast.Projection_Preprocessing(Ax(np.array([cur])))[:,0]
        try:
            cur = forcast.roughRegistration(cur, real_img, proj_d, feature_params, Ax, c=0)
            #print(cur)
        except Exception as ex:
            print(ex)
            #raise
        corrs.append(cur)
        
    
    #corrs = np.array(corrs)
    corrs = np.array(params)
    global_cor = np.median(corrs, axis=0)

    #print(corrs)

    for i in range(len(ims)):
        print(i, end=" ", flush=True)
        try:
            cur = corrs[i]
            proj_d = forcast.Projection_Preprocessing(Ax(np.array([cur])))[:,0]
            real_img = forcast.Projection_Preprocessing(ims[i])
            cur = forcast.lessRoughRegistration(cur, real_img, proj_d, feature_params, Ax)
            corrs[i] = cur
        except Exception as ex:
            print(ex)
            raise
    
    return corrs

def reg_bfgs(ims, params, Ax, feature_params, eps = [1,1,1,0.1,0.1,0.1], my = True):
   
    #corrs = reg_rough(ims, params, Ax, feature_params)
    corrs = np.array(params)
    for i in range(len(ims)):
        print(i, end=" ", flush=True)
        try:
            for _ in range(1):
                bfgs_corrs, fun, err = forcast.bfgs(i, ims, corrs, Ax, feature_params, np.array(eps), my=my)
                corrs[i] = bfgs_corrs
        except Exception as ex:
            print(ex)
            raise
    
    return corrs

def create_circular_mask(shape, center=None, radius=None, radius_off=5, end_off=30):
    l, h, w = shape
    if center is None: # use the middle of the image
        center = (int(w/2), int(h/2))
    if radius is None: # use the smallest distance between the center and image walls
        radius = min(center[0], center[1], w-center[0], h-center[1])

    Y, X = np.ogrid[:h, :w]
    dist_from_center = np.sqrt((X - center[0])**2 + (Y-center[1])**2)

    mask = np.zeros((l,h,w), dtype=bool)
    mask[:] = (dist_from_center <= (radius-radius_off))[np.newaxis,:,:]
    for i in range(30):
        mask[i] = (dist_from_center <= (i/30)*(radius-radius_off))
        mask[-i-1] = (dist_from_center <= (i/30)*(radius-radius_off))

    return mask

def reg_and_reco(ims, in_params, config):
    name = config["name"]
    grad_width = config["grad_width"] if "grad_width" in config else (1,25)
    perf = config["perf"] if "perf" in config else False
    Ax = config["Ax"]
    method = config["method"]
    real_image = config["real_cbct"]

    print(name, grad_width)
    params = np.array(in_params[:])
    if not perf and not os.path.exists(os.path.join("recos", "forcast_"+name.split('_',1)[0]+"_reco-input.nrrd")):
        sitk.WriteImage(sitk.GetImageFromArray(real_image)*100, os.path.join("recos", "forcast_"+name.split('_',1)[0]+"_reco-input.nrrd"))
    if not perf:
        sino = sitk.GetImageFromArray(np.swapaxes(ims,0,1))
        sitk.WriteImage(sino, os.path.join("recos", "forcast_"+name+"_projs-input.nrrd"))
        del sino
    if not perf:# and not os.path.exists(os.path.join("recos", "forcast_"+name+"_sino-input.nrrd")):
        sino = Ax(params)
        sino = sitk.GetImageFromArray(sino)
        sitk.WriteImage(sino, os.path.join("recos", "forcast_"+name+"_sino-input.nrrd"))
        del sino
    if not perf:# and not os.path.exists(os.path.join("recos", "forcast_"+name+"_reco-input.nrrd")):
        reg_geo = Ax.create_geo(params)
        rec = utils.FDK_astra(real_image.shape, reg_geo)(np.swapaxes(ims, 0,1), True)
        #mask = np.zeros(rec.shape, dtype=bool)
        mask = create_circular_mask(rec.shape)
        rec = rec*mask
        del mask
        #rec = utils.CGLS_astra(real_image.shape, reg_geo)(np.swapaxes(ims, 0,1), 10)
        rec = sitk.GetImageFromArray(rec)*100
        sitk.WriteImage(rec, os.path.join("recos", "forcast_"+name+"_reco-input.nrrd"))
        del rec

    cali = {}
    cali['feat_thres'] = 80
    cali['iterations'] = 50
    cali['confidence_thres'] = 0.025
    cali['relax_factor'] = 0.3
    cali['match_thres'] = 60
    cali['max_ratio'] = 0.9
    cali['max_distance'] = 20
    cali['outlier_confidence'] = 85

    np.seterr(all='raise') 

    if method==1:
        perftime = time.perf_counter()

        corrs = reg_lessrough(ims, params, Ax, {'feat_thres': cali['feat_thres']})
        
        vecs = Ax.create_vecs(corrs)
        write_vectors(name+"-lessrough-corr", corrs)
        write_vectors(name+"-lessrough", vecs)

        perftime = time.perf_counter()-perftime
        print("lessrough reg done ", perftime)

        if not perf:
            reg_geo = Ax.create_geo(corrs)
            sino = Ax(corrs)
            sino = sitk.GetImageFromArray(sino)
            sitk.WriteImage(sino, os.path.join("recos", "forcast_"+name+"_sino-lessrough.nrrd"))
            del sino
            rec = utils.FDK_astra(real_image.shape, reg_geo)(np.swapaxes(ims, 0,1))
            #rec = np.swapaxes(rec, 0, 2)
            #rec = np.swapaxes(rec, 1,2)
            #rec = rec[::-1, ::-1]
            rec = sitk.GetImageFromArray(rec)*100
            sitk.WriteImage(rec, os.path.join("recos", "forcast_"+name+"_reco-lessrough.nrrd"))
            del rec
            
    elif method==2:
        perftime = time.perf_counter()
        
        good_values= [
            #[7,0.2,0.01],
            #[1,1,0.1],
            [1,7,0.001],
            #[1,7,0.5],
            #[0.2,1,0.08],
            #[5,7.33333333,0.05],
            #[5,17,0.05],
        ]
        for xy,z,r in good_values:
            perftime = time.perf_counter()
            
            corrs = reg_bfgs(ims, params, Ax, {'feat_thres': cali['feat_thres']}, eps = [xy,xy,z,r,r,r])
                
            perftime = time.perf_counter()-perftime
            print("reg done ", xy, z, r, perftime)
            
            if not perf:
                vecs = Ax.create_vecs(corrs)
                write_vectors(name+"-bfgs-"+str(xy)+"_"+str(z)+"_"+str(r), vecs)
                write_vectors(name+"-bfgs-"+str(xy)+"_"+str(z)+"_"+str(r)+"-corrs", corrs)

                reg_geo = Ax.create_geo(corrs)
                sino = Ax(corrs)
                sitk.WriteImage(sitk.GetImageFromArray(sino), os.path.join("recos", "forcast_"+name+"_sino-"+str(xy)+"_"+str(z)+"_"+str(r)+".nrrd"))
                del sino
                rec = utils.FDK_astra(real_image.shape, reg_geo)(np.swapaxes(ims, 0,1))
                #rec = np.swapaxes(rec, 0, 2)
                #rec = np.swapaxes(rec, 1,2)
                #rec = rec[::-1, ::-1]
                sitk.WriteImage(sitk.GetImageFromArray(rec)*100, os.path.join("recos", "forcast_"+name+"_reco-"+str(xy)+"_"+str(z)+"_"+str(r)+".nrrd"))
                del rec

    elif method==6:
        perftime = time.perf_counter()
        
        good_values= [
            #[7,0.2,0.01],
            #[1,1,0.1],
            [1,7,0.001],
            #[1,7,0.5],
            #[0.2,1,0.08],
            #[5,7.33333333,0.05],
            #[5,17,0.05],
        ]
        for xy,z,r in good_values:
            perftime = time.perf_counter()
            
            corrs = reg_bfgs(ims, params, Ax, {'feat_thres': cali['feat_thres']}, eps = [xy,xy,z,r,r,r], my = False)
                
            perftime = time.perf_counter()-perftime
            print("reg done ", xy, z, r, perftime)
            
            if not perf:
                vecs = Ax.create_vecs(corrs)
                write_vectors(name+"-bfgs-"+str(xy)+"_"+str(z)+"_"+str(r), vecs)
                write_vectors(name+"-bfgs-"+str(xy)+"_"+str(z)+"_"+str(r)+"-corrs", corrs)

                reg_geo = Ax.create_geo(corrs)
                sino = Ax(corrs)
                sitk.WriteImage(sitk.GetImageFromArray(sino), os.path.join("recos", "forcast_"+name+"_sino-"+str(xy)+"_"+str(z)+"_"+str(r)+".nrrd"))
                del sino
                rec = utils.FDK_astra(real_image.shape, reg_geo)(np.swapaxes(ims, 0,1))
                #rec = np.swapaxes(rec, 0, 2)
                #rec = np.swapaxes(rec, 1,2)
                #rec = rec[::-1, ::-1]
                sitk.WriteImage(sitk.GetImageFromArray(rec)*100, os.path.join("recos", "forcast_"+name+"_reco-"+str(xy)+"_"+str(z)+"_"+str(r)+".nrrd"))
                del rec
                
    else:
        perftime = time.perf_counter()
        
        if False and mp.cpu_count() > 1:
            corrs = reg_rough_parallel(ims, params, config, method)
        else:
            corrs = reg_rough(ims, params, config, method)

        vecs = Ax.create_vecs(corrs)
        write_vectors(name+"-rough-corr", corrs)
        write_vectors(name+"-rough", vecs)

        perftime = time.perf_counter()-perftime
        print("rough reg done ", perftime)

        if not perf:
            reg_geo = Ax.create_geo(corrs)
            sino = Ax(corrs)
            sino = sitk.GetImageFromArray(sino)
            sitk.WriteImage(sino, os.path.join("recos", "forcast_"+name+"_sino-rough.nrrd"))
            del sino
            rec = utils.FDK_astra(real_image.shape, reg_geo)(np.swapaxes(ims, 0,1))
            mask = create_circular_mask(rec.shape)
            rec = rec*mask
            del mask
            #rec = np.swapaxes(rec, 0, 2)
            #rec = np.swapaxes(rec, 1,2)
            #rec = rec[::-1, ::-1]
            rec = sitk.GetImageFromArray(rec)*100
            sitk.WriteImage(rec, os.path.join("recos", "forcast_"+name+"_reco-rough.nrrd"))
            del rec

    return vecs, corrs

def parameter_search(proj_path, cbct_path):
    ims, ims_ungained, i0s, i0s_ungained, angles, coord_systems, sids, sods = read_dicoms(proj_path, max_ims=1)

    origin, size, spacing, image = utils.read_cbct_info(cbct_path)
    real_image = utils.fromHU(sitk.GetArrayFromImage(image))

    real_image = real_image[::-1, ::-1]
    real_image = np.swapaxes(real_image, 1,2)
    real_image = np.swapaxes(real_image, 0, 2)

    detector_shape = np.array((1920,2480))
    detector_mult = np.floor(detector_shape / np.array(ims_ungained.shape[1:]))
    detector_shape = np.array(ims_ungained.shape[1:])
    detector_spacing = np.array((0.125, 0.125)) * detector_mult

    cali = {}
    cali['feat_thres'] = 80
    cali['iterations'] = 50
    cali['confidence_thres'] = 0.025
    cali['relax_factor'] = 0.3
    cali['match_thres'] = 60
    cali['max_ratio'] = 0.9
    cali['max_distance'] = 20
    cali['outlier_confidence'] = 85

    geo, (prims, secs), _ = utils.create_astra_geo_coords(coord_systems, detector_spacing, detector_shape, sods, sids-sods, 1.2/np.min(spacing))

    #print(angles, prims, secs)

    input_sino = np.swapaxes(ims,0,1)
    sitk.WriteImage(sitk.GetImageFromArray(input_sino), os.path.join("recos", "forcast_input.nrrd"))

    Ax = utils.Ax_geo_astra(real_image.shape, real_image)
    sino = Ax(geo)
    sitk.WriteImage(sitk.GetImageFromArray(sino), os.path.join("recos", "forcast_sino.nrrd"))

    geo_d = astra.create_proj_geom('cone_vec', detector_shape[0], detector_shape[1], geo['Vectors'][0:1])
    proj_d = forcast.Projection_Preprocessing(Ax(geo_d))[:,0]
    real_img = forcast.Projection_Preprocessing(ims[0])
    cur, _scale = forcast.roughRegistration(np.array([0,0,0,0,0.0]), real_img, proj_d, {'feat_thres': cali['feat_thres']}, geo['Vectors'][0])
    vec = forcast.applyChange(geo['Vectors'][0], cur)
    vecs = np.array([vec])
    geo_d = astra.create_proj_geom('cone_vec', detector_shape[0], detector_shape[1], vecs)
    proj_d = forcast.Projection_Preprocessing(Ax(geo_d))
    sitk.WriteImage(sitk.GetImageFromArray(proj_d), os.path.join("recos", "forcast_rough.nrrd"))

    with open('stats.csv','w') as f:
        good_values= [
            [7,0.2,0.01],
            [0.5,7,0.1],
            [0.2,1,0.08],
            [5,7.33333333,0.05],
            [5,17,0.05],
        ]
        for xy,z,r in good_values:
            for i in range(len(ims)):
                print("Projection ", i)
                funs = []
                try:
                    perftime = time.perf_counter()
                    bfgs_vecs, fun, err = forcast.bfgs(i, ims, real_image, cali, geo, real_image.shape, Ax, np.array([xy, xy, z, r, r]))
                    perftime = time.perf_counter()-perftime
                    print(int(fun), int(err), xy, z, r, perftime)
                    funs.append([xy,z,r,fun,err,perftime])
                    f.write(",".join([str(e) for e in [xy,z,r,fun,err,perftime]])+"\n")
                    if fun < 12000:
                        bfgs_geo = astra.create_proj_geom('cone_vec', detector_shape[0], detector_shape[1], np.array([bfgs_vecs]))
                        sino = Ax(bfgs_geo)
                        #sitk.WriteImage(sitk.GetImageFromArray(input_sino), os.path.join("recos", "forcast_input.nrrd"))
                        sitk.WriteImage(sitk.GetImageFromArray(sino), os.path.join("recos", "forcast_sino_bfgs--"+str(fun)+"--"+str(xy)+"_"+str(z)+"_"+str(r)+".nrrd"))
                except Exception as ex:
                    print(ex)
            for xy in itertools.chain(np.linspace(0.1,1,10), np.linspace(1,10,10)):
                #for m1 in np.linspace(3,100, 5):
                for z in itertools.chain(np.linspace(0.1,1,10), np.linspace(1,10,10)):
                    for r in itertools.chain(np.linspace(0.01,0.1,10), np.linspace(0.1,10,10)):
                        #print(e, m)
                        try:
                            perftime = time.perf_counter()
                            bfgs_vecs, fun, err = forcast.bfgs(i, ims, real_image, cali, geo, real_image.shape, Ax, np.array([xy,xy,z,r,r]))
                            perftime = time.perf_counter()-perftime
                            print(int(fun), int(err), xy, z, r, perftime)
                            funs.append([xy,z,r,fun,err,perftime])
                            f.write(",".join([str(e) for e in [xy,z,r,fun,err,perftime]])+"\n")
                            if fun < 12000:
                                bfgs_geo = astra.create_proj_geom('cone_vec', detector_shape[0], detector_shape[1], np.array([bfgs_vecs]))
                                sino = Ax(bfgs_geo)
                                #sitk.WriteImage(sitk.GetImageFromArray(input_sino), os.path.join("recos", "forcast_input.nrrd"))
                                sitk.WriteImage(sitk.GetImageFromArray(sino), os.path.join("recos", "forcast_sino_bfgs--"+str(fun)+"--"+str(xy)+"_"+str(z)+"_"+str(r)+".nrrd"))
                        except Exception as ex:
                            print(ex)
        #with open('stats.csv','w') as f:
            #f.writelines([",".join([str(e) for e in line])+"\n" for line in funs])
        my_vecs = forcast.FORCAST(i, ims, real_image, cali, geo, real_image.shape, np.array([2.3,2.3*3,2.3,2.3*0.1,2.3*0.1]), Ax)
        my_geo = astra.create_proj_geom('cone_vec', detector_shape[0], detector_shape[1], np.array([my_vecs]))
        sino = Ax(my_geo)
        sitk.WriteImage(sitk.GetImageFromArray(sino), os.path.join("recos", "forcast_sino_my.nrrd"))

    rec = utils.FDK_astra(real_image.shape, geo)(np.swapaxes(ims, 0,1))
    rec = np.swapaxes(rec, 0, 2)
    rec = np.swapaxes(rec, 1,2)
    rec = rec[::-1, ::-1]
    sitk.WriteImage(sitk.GetImageFromArray(rec), os.path.join("recos", "forcast_reco.nrrd"))

def reg_real_data():
    projs = []

    if os.path.exists("E:\\output"):
        prefix = r"E:\output"
    else:
        prefix = r"D:\lumbal_spine_13.10.2020\output"

    cbct_path = prefix + r"\CKM4Baltimore2019\20191108-081024.994000\DCT Head Clear Nat Fill Full HU Normal [AX3D]"
    projs += [
    #('191108_balt_cbct_', prefix + '\\CKM4Baltimore2019\\20191108-081024.994000\\20sDCT Head 70kV', cbct_path),
    #('191108_balt_all_', prefix + '\\CKM4Baltimore2019\\20191108-081024.994000\\DR Overview', cbct_path),
    ]
    cbct_path = prefix + r"\CKM4Baltimore2019\20191107-091105.486000\DCT Head Clear Nat Fill Full HU Normal [AX3D]"
    projs += [
    #('191107_balt_sin1_', prefix + '\\CKM4Baltimore2019\\20191107-091105.486000\\Sin1', cbct_path),
    #('191107_balt_sin2_', prefix + '\\CKM4Baltimore2019\\20191107-091105.486000\\Sin2', cbct_path),
    #('191107_balt_sin3_', prefix + '\\CKM4Baltimore2019\\20191107-091105.486000\\Sin3', cbct_path),
    #('191107_balt_cbct_', prefix + '\\CKM4Baltimore2019\\20191107-091105.486000\\20sDCT Head 70kV', cbct_path),
    ]
    cbct_path = prefix + r"\CKM_LumbalSpine\20201020-151825.858000\DCT Head Clear Nat Fill Full HU Normal [AX3D] 70kV"
    #cbct_path = prefix + r"\CKM_LumbalSpine\20201020-093446.875000\DCT Head Clear Nat Fill Full HU Normal [AX3D] 70kV"
    projs += [
    ('201020_imbu_cbct_', prefix + '\\CKM_LumbalSpine\\20201020-093446.875000\\20sDCT Head 70kV', cbct_path),
    #('201020_imbu_sin_', prefix + '\\CKM_LumbalSpine\\20201020-122515.399000\\P16_DR_LD', cbct_path),
    ('201020_imbu_opti_', prefix + '\\CKM_LumbalSpine\\20201020-093446.875000\\P16_DR_LD', cbct_path),
    ('201020_imbu_circ_', prefix + '\\CKM_LumbalSpine\\20201020-140352.179000\\P16_DR_LD', cbct_path),
    ('201020_imbureg_noimbu_cbct_', prefix + '\\CKM_LumbalSpine\\20201020-151825.858000\\20sDCT Head 70kV', cbct_path),
    ('201020_imbureg_noimbu_opti_', prefix + '\\CKM_LumbalSpine\\20201020-152349.323000\\P16_DR_LD', cbct_path),
    ]
    cbct_path = prefix + r"\CKM4Baltimore\CBCT_2021_01_11_16_04_12"
    projs += [
    #('210111_balt_cbct_', prefix + '\\CKM4Baltimore\\CBCT_SINO', cbct_path),
    #('210111_balt_circ_', prefix + '\\CKM4Baltimore\\Circle_Fluoro', cbct_path),
    ]
    cbct_path = prefix + r"\CKM\CBCT\20201207-093148.064000-DCT Head Clear Nat Fill Full HU Normal [AX3D]"
    projs += [
    ('201207_cbct_', prefix + '\\CKM\\CBCT\\20201207-093148.064000-20sDCT Head 70kV', cbct_path),
    ('201207_circ_', prefix + '\\CKM\\Circ Tomo 2. Versuch\\20201207-105441.287000-P16_DR_HD', cbct_path),
    #('201207_eight_', prefix + '\\CKM\\Eight die Zweite\\20201207-143732.946000-P16_DR_HD', cbct_path),
    #('201207_opti_', prefix + '\\CKM\\Opti Traj\\20201207-163001.022000-P16_DR_HD', cbct_path),
    ('201207_sin_', prefix + '\\CKM\\Sin Traj\\20201207-131203.754000-P16_Card_HD', cbct_path),
    #('201207_tomo_', prefix + '\\CKM\\Tomo\\20201208-110616.312000-P16_DR_HD', cbct_path),
    ]
    
    for name, proj_path, cbct_path in projs:
        
        try:
            ims, _, _, _, _, coord_systems, sids, sods = read_dicoms(proj_path, max_ims=200)
            #ims = ims[:20]
            #coord_systems = coord_systems[:20]
            skip = max(1, int(len(ims)/200))
            random = np.random.default_rng(23)
            angles_noise = random.normal(loc=0, scale=0.5, size=(len(ims), 3))
            angles_noise = np.zeros_like(angles_noise)
            trans_noise = random.normal(loc=0, scale=10, size=(len(ims), 3))

            #skip = 4
            ims = ims[::skip]
            coord_systems = coord_systems[::skip]
            sids = np.mean(sids[::skip])
            sods = np.mean(sods[::skip])
            angles_noise = angles_noise[::skip]
            trans_noise = trans_noise[::skip]

            origin, size, spacing, image = utils.read_cbct_info(cbct_path)

            detector_shape = np.array((1920,2480))
            detector_mult = np.floor(detector_shape / np.array(ims.shape[1:]))
            detector_shape = np.array(ims.shape[1:])
            detector_spacing = np.array((0.125, 0.125)) * detector_mult

            real_image = utils.fromHU(sitk.GetArrayFromImage(image))
            del image

            Ax = utils.Ax_param_asta(real_image.shape, detector_spacing, detector_shape, sods, sids-sods, 1.2/np.min(spacing), real_image)
            Ax_gen = (real_image.shape, detector_spacing, detector_shape, sods, sids-sods, 1.2/np.min(spacing), real_image)
            geo = utils.create_astra_geo_coords(coord_systems, detector_spacing, detector_shape, sods, sids-sods, 1.2/np.min(spacing))

            r = utils.rotMat(90, [1,0,0]).dot(utils.rotMat(-90, [0,0,1]))

            params = np.zeros((len(geo['Vectors']), 3, 3), dtype=float)
            params[:,1] = np.array([r.dot(v) for v in geo['Vectors'][:, 6:9]])
            params[:,2] = np.array([r.dot(v) for v in geo['Vectors'][:, 9:12]])

            for i, (α,β,γ) in enumerate(angles_noise):
                params[i] = cal.applyRot(params[i], -α, -β, -γ)

            projs = Ax(params)
            #sitk.WriteImage(sitk.GetImageFromArray(projs), "recos/projs.nrrd")
            #sitk.WriteImage(sitk.GetImageFromArray(np.swapaxes(ims,0,1)), "recos/ims.nrrd")

            config = {"Ax": Ax, "Ax_gen": Ax_gen, "method": 3, "name": name, "real_cbct": real_image}

            #for method in [3,4,5,0,6]:
            for method in [3]:
                config["name"] = name + str(method)
                config["method"] = method
                config["noise"] = (np.zeros((len(ims),3)), np.array(angles_noise))
                vecs, corrs = reg_and_reco(ims, params, config)
        except Exception as e:
            print(name, "cali failed", e)
            raise

if __name__ == "__main__":
    #import cProfile, io, pstats
    #profiler = cProfile.Profile()
    #profiler.enable()
    reg_real_data()
    #profiler.disable()
    #s = io.StringIO()
    #sortby = pstats.SortKey.TIME
    #ps = pstats.Stats(profiler, stream=s).sort_stats(sortby)
    #ps.print_stats(20)
    #print(s.getvalue())