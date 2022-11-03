import pickle
import numpy as np
import random
import torch
import os
import sys

# from knowledge_graph import KnowledgeGraph
# from data_process import LastFmDataset
# from KG_data_generate.lastfm_small_data_process import LastFmSmallDataset
# from KG_data_generate.lastfm_knowledge_graph import KnowledgeGraph
# Dataset names
LAST_FM = 'LAST_FM'
LAST_FM_STAR = 'LAST_FM_STAR'
YELP = 'YELP'
YELP_STAR = 'YELP_STAR'

RAW_DATA_DIR = {
    LAST_FM: './datasets/raw_data/lastfm',
    YELP: './datasets/raw_data/yelp',
    LAST_FM_STAR: './datasets/raw_data/lastfm_star',
    YELP_STAR: './datasets/raw_data/yelp',
}
PROCESSED_DATA_DIR = {
    LAST_FM: './datasets/processed_data/last_fm',
    YELP: './datasets/processed_data/yelp',
    LAST_FM_STAR: './datasets/processed_data/last_fm_star',
    YELP_STAR: './datasets/processed_data/yelp_star',
}
CHECKPOINT_DIR = {
    LAST_FM: './checkpoints/last_fm',
    YELP: './checkpoints/yelp',
    LAST_FM_STAR: './checkpoints/last_fm_star',
    YELP_STAR: './checkpoints/yelp_star',
}


def cuda_(var):
    return var.cuda() if torch.cuda.is_available() else var


def save_dataset(dataset, dataset_obj):
    dataset_file = PROCESSED_DATA_DIR[dataset] + '/dataset.pkl'
    with open(dataset_file, 'wb') as f:
        pickle.dump(dataset_obj, f)


def load_dataset(dataset):
    dataset_file = PROCESSED_DATA_DIR[dataset] + '/dataset.pkl'
    dataset_obj = pickle.load(open(dataset_file, 'rb'))
    return dataset_obj


def save_kg(dataset, kg):
    kg_file = PROCESSED_DATA_DIR[dataset] + '/kg.pkl'
    pickle.dump(kg, open(kg_file, 'wb'))


def load_kg(dataset):
    kg_file = PROCESSED_DATA_DIR[dataset] + '/kg.pkl'
    kg = pickle.load(open(kg_file, 'rb'))
    return kg


def save_graph(dataset, graph):
    graph_file = PROCESSED_DATA_DIR[dataset] + '/graph.pkl'
    pickle.dump(graph, open(graph_file, 'wb'))


def load_graph(dataset):
    graph_file = PROCESSED_DATA_DIRR[dataset] + '/graph.pkl'
    graph = pickle.load(open(graph_file, 'rb'))
    return graph


def load_embed(dataset, embed):
    if embed:
        path = PROCESSED_DATA_DIR[dataset] + '/embeds/' + '{}.pkl'.format(embed)
    else:
        return None
    with open(path, 'rb') as f:
        embeds = pickle.load(f)
        print('{} Embedding load successfully!'.format(embed))
        return embeds


def load_rl_agent(dataset, filename, epoch_user):
    model_file = CHECKPOINT_DIR[dataset] + '/model/' + filename + '-epoch-{}.pkl'.format(epoch_user)
    model_dict = torch.load(model_file)
    print('RL policy model load at {}'.format(model_file))
    return model_dict


def save_rl_agent(dataset, model, filename, epoch_user):
    model_file = CHECKPOINT_DIR[dataset] + '/model/' + filename + '-epoch-{}.pkl'.format(epoch_user)
    if not os.path.isdir(CHECKPOINT_DIR[dataset] + '/model/'):
        os.makedirs(CHECKPOINT_DIR[dataset] + '/model/')
    torch.save(model, model_file)
    print('RL policy model saved at {}'.format(model_file))


def save_rl_mtric(dataset, filename, epoch, SR, spend_time, mode='train'):
    PATH = CHECKPOINT_DIR[dataset] + '/log/' + filename + '.txt'
    if not os.path.isdir(CHECKPOINT_DIR[dataset] + '/log/'):
        os.makedirs(CHECKPOINT_DIR[dataset] + '/log/')
    if mode == 'train':
        with open(PATH, 'a') as f:
            f.write('===========Train===============\n')
            f.write('Starting {} user epochs\n'.format(epoch))
            f.write('training SR@5: {}\n'.format(SR[0]))
            f.write('training SR@10: {}\n'.format(SR[1]))
            f.write('training SR@15: {}\n'.format(SR[2]))
            f.write('training Avg@T: {}\n'.format(SR[3]))
            f.write('training hDCG: {}\n'.format(SR[4]))
            f.write('Spending time: {}\n'.format(spend_time))
            f.write('================================\n')
            # f.write('1000 loss: {}\n'.format(loss_1000))
    elif mode == 'test':
        with open(PATH, 'a') as f:
            f.write('===========Test===============\n')
            f.write('Testing {} user tuples\n'.format(epoch))
            f.write('Testing SR@5: {}\n'.format(SR[0]))
            f.write('Testing SR@10: {}\n'.format(SR[1]))
            f.write('Testing SR@15: {}\n'.format(SR[2]))
            f.write('Testing Avg@T: {}\n'.format(SR[3]))
            f.write('Testing hDCG: {}\n'.format(SR[4]))
            f.write('Testing time: {}\n'.format(spend_time))
            f.write('================================\n')
            # f.write('1000 loss: {}\n'.format(loss_1000))


def save_rl_model_log(dataset, filename, epoch, epoch_loss, train_len):
    PATH = CHECKPOINT_DIR[dataset] + '/log/' + filename + '.txt'
    if not os.path.isdir(CHECKPOINT_DIR[dataset] + '/log/'):
        os.makedirs(CHECKPOINT_DIR[dataset] + '/log/')
    with open(PATH, 'a') as f:
        f.write('Starting {} epoch\n'.format(epoch))
        f.write('training loss : {}\n'.format(epoch_loss / train_len))
        # f.write('1000 loss: {}\n'.format(loss_1000))


def set_random_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


# Disable
def blockPrint():
    sys.stdout = open(os.devnull, 'w')


# Restore
def enablePrint():
    sys.stdout = sys.__stdout__


def set_cuda(args):
    use_cuda = torch.cuda.is_available()
    if use_cuda:
        torch.cuda.manual_seed(args.seed)
        torch.backends.cudnn.deterministic = True
    devices_id = [int(device_id) for device_id in args.gpu.split()]
    device = (
        torch.device("cuda:{}".format(str(devices_id[0])))
        if use_cuda
        else torch.device("cpu")
    )
    return device, devices_id