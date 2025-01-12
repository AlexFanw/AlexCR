import math
import time

import numpy as np
from tqdm import tqdm
from collections import namedtuple
import argparse
import statistics

from rl.agent.ask_agent import AskAgent
from rl.agent.rec_agent import RecAgent
from rl.rl_memory import ReplayMemoryPER
from rl.network.network_value import ValueNetwork
from utils.utils import *
from rl.recommend_env.env_variable_question import VariableRecommendEnv
from rl.rl_evaluate import rl_evaluate
from graph.gcn import GraphEncoder
import warnings

warnings.filterwarnings("ignore")

Transition = namedtuple('Transition',
                        ('state', 'action', 'next_state', 'reward', 'next_cand'))


def choose_option(ask_agent, rec_agent, state, cand, option_strategy=0, decay_step=0):
    if cand["feature"] == [] or len(cand["item"]) < 10:
        return 0  # Recommend
    with torch.no_grad():
        state_emb = ask_agent.gcn_net([state])
        feature_cand = cand["feature"]
        ask_score = []
        value = ask_agent.value_net(state_emb).detach().cpu().numpy().squeeze()
        for feature in feature_cand:
            feature = torch.LongTensor(np.array(feature).astype(int).reshape(-1, 1)).to(ask_agent.device)  # [N*1]
            feature = ask_agent.gcn_net.embedding(feature)
            ask_score.append(
                 value + ask_agent.policy_net(state_emb, feature, choose_action=False).detach().cpu().numpy().squeeze())
        ask_prop = np.exp(ask_score) / sum(np.exp(ask_score))
        if option_strategy == 0:
            ask_Q = np.array(ask_score).dot(ask_prop)
        else:
            ask_Q = max(ask_score)

        state_emb = rec_agent.gcn_net([state])
        item_cand = cand["item"]
        rec_score = []
        value = rec_agent.value_net(state_emb).detach().cpu().numpy().squeeze()
        for item in item_cand:
            item = torch.LongTensor(np.array(item).astype(int).reshape(-1, 1)).to(rec_agent.device)  # [N*1]
            item = rec_agent.gcn_net.embedding(item)
            rec_score.append(
                 value + rec_agent.policy_net(state_emb, item, choose_action=False).detach().cpu().numpy().squeeze())
        rec_prop = np.exp(rec_score) / sum(np.exp(rec_score))
        
        if option_strategy == 0:
            rec_Q = np.array(rec_score).dot(rec_prop)
        else:
            rec_Q = max(rec_score)
        print("\n**CHOOSE OPTION** ASK VALUE:{}, REC VALUE:{}\n".format(ask_Q, rec_Q))
        
        EPS_START=0.9
        EPS_END=0.1
        EPS_DECAY=0.0001
        eps_threshold = EPS_END + (EPS_START - EPS_END) * \
                        math.exp(-1. * decay_step * EPS_DECAY)
        soft_random = random.random()
        # print(decay_step)
        print("eps_threshold:{}".format(eps_threshold))
        if soft_random > eps_threshold:
            if ask_Q > rec_Q:
                return 1
            else:
                return 0
        else:
            return random.randint(0, 1)


def calculate_hdcg_item(t, done):
    return 1 / math.log(t + 2, 2) + (1 / math.log(t + 1, 2) - 1 / math.log(t + 2, 2)) / math.log(done + 1, 2)


def calculate_hdcg_attribute(t, i):
    return 1 / math.log(t + 2, 2) + (1 / math.log(t + 1, 2) - 1 / math.log(t + 2, 2)) / math.log(i + 2, 2)


def option_critic_pipeline(args, kg, dataset, filename):
    
    # Prepare the Environment
    set_random_seed(args.seed)
    env = VariableRecommendEnv(kg, dataset,
                               args.data_name, args.embed, seed=args.seed, max_turn=args.max_turn,
                               cand_feature_num=args.cand_feature_num, cand_item_num=args.cand_item_num,
                               attr_num=args.attr_num, mode='train',
                               entropy_way=args.entropy_method)

    # User&Feature Embedding
    embed = torch.FloatTensor(np.concatenate((env.ui_embeds, env.feature_emb, np.zeros((1, env.ui_embeds.shape[1]))), axis=0))
    # print(embed.size(0), embed.size(1))
    '''
    VALUE NET
    '''
    value_net = ValueNetwork().to(args.device)
    '''
    GCN NET
    '''
    gcn_net = GraphEncoder(device=args.device, entity=embed.size(0), emb_size=embed.size(1), kg=kg,
                           embeddings=embed, fix_emb=args.fix_emb, seq=args.seq, gcn=args.gcn,
                           hidden_size=args.hidden_size).to(args.device)
    '''
    ASK AGENT
    '''
    
    # Ask Memory
    ask_memory = ReplayMemoryPER(args.memory_size)  # 50000
    # Ask Agent
    ask_agent = AskAgent(device=args.device, memory=ask_memory, action_size=embed.size(1),
                         hidden_size=args.hidden_size, gcn_net=gcn_net, learning_rate=args.learning_rate,
                         l2_norm=args.l2_norm, PADDING_ID=embed.size(0) - 1, value_net=value_net, alpha=args.alpha)
    '''
    REC AGENT
    '''
    
    # Rec Memory
    rec_memory = ReplayMemoryPER(args.memory_size)  # 50000
    # Rec Agent
    rec_agent = RecAgent(device=args.device, memory=rec_memory, action_size=embed.size(1),
                         hidden_size=args.hidden_size, gcn_net=gcn_net, learning_rate=args.learning_rate,
                         l2_norm=args.l2_norm, PADDING_ID=embed.size(0) - 1, value_net=value_net, alpha=args.alpha)
    # load parameters
    if args.load_rl_epoch != 0:
        print('Loading Model in epoch {}'.format(args.load_rl_epoch))
        ask_agent.load_model(data_name=args.data_name, filename=filename, epoch_user=args.load_rl_epoch)
        rec_agent.load_model(data_name=args.data_name, filename=filename, epoch_user=args.load_rl_epoch)
        value_net.load_value_net(data_name=args.data_name, filename=filename, epoch_user=args.load_rl_epoch)

    decay_step = 0
    for epoch in range(1 + args.load_rl_epoch, args.max_epoch + 1):
        tt = time.time()
        start = tt
        print("\nEpoch: {}, Total: {}".format(epoch, args.max_epoch))
        HDCG_item, total_reward = 0., 0.
        AvgT_list = []
        Suc_Turn_list = []
        rec_step_list = []
        ask_step_list = []
        HDCG_attribute_list = []

        rec_loss = []
        ask_loss = []
        rec_state_infer_loss = []
        ask_state_infer_loss = []
        if args.block_print:
            blockPrint()
        for episode in tqdm(range(args.sample_times), desc='sampling'):
            print('\n================Epoch:{} Episode:{}===================='.format(epoch, episode))
            state, cand, action_space = env.reset()
            epi_reward = 0
            done = 0
            for t in range(1, args.max_turn+1):  # Turn
                '''
                Over Option: Select Ask / Rec
                '''
                env.cur_conver_step = 1
                if done:
                    break
                decay_step += 1
                option = choose_option(ask_agent, rec_agent, state, cand, args.option_strategy, decay_step)

                '''
                Intra Option: Select features / items
                '''
                # ASK
                if option == 1:
                    print("\n————————Turn: ", t, "  Option: ASK————————")
                    termination = False
                    ask_score = []
                    while not termination and not done:
                        # Select Action
                        chosen_feature = ask_agent.select_action(state, cand["feature"], action_space["feature"])
                        # Env Interaction
                        next_state, next_cand, action_space, reward, done = env.step(chosen_feature.item(), None, mode="train")
                        # Reward Collection
                        epi_reward += reward
                        ask_score.append(reward)

                        # Whether Termination
                        next_state_emb = ask_agent.gcn_net([next_state])
                        term_score = ask_agent.termination_net(next_state_emb).item()
                        print("Termination Score:", term_score)
                        if term_score >= 0.5 or next_cand["feature"] == [] or env.cur_conver_step > args.max_ask_step:
                            termination = True

                        # reward
                        if (termination or done) and t == env.max_turn:
                            reward += env.reward_dict["quit"]
                        reward_ = torch.tensor([reward], device=args.device, dtype=torch.float)

                        # Push memory
                        ask_agent.memory.push(state, chosen_feature.cpu(), next_state, reward_.cpu(),
                                              next_cand["item"], next_cand["feature"])
                        state = next_state
                        cand = next_cand

                        # After Done
                        if done or (termination and t == env.max_turn):
                            AvgT_list.append(t)
                            total_reward += epi_reward
                            break

                    # calculate HDCG Attribute
                    for i in range(len(ask_score)):
                        if ask_score[i] > 0:
                            HDCG_attribute_list.append(calculate_hdcg_attribute(t, i))
                    # Optimize
                    loss, loss_state = ask_agent.optimize_model(args.batch_size, args.gamma, rec_agent, args.term_reg)
                    if loss is not None:
                        ask_loss.append(loss)
                        ask_state_infer_loss.append(loss_state)
                    
                    loss, loss_state = rec_agent.optimize_model(args.batch_size, args.gamma, ask_agent, args.term_reg)
                    if loss is not None:
                        rec_loss.append(loss)
                        rec_state_infer_loss.append(loss_state)

                # RECOMMEND
                elif option == 0:
                    print("\n————————Turn: ", t, "  Option: REC————————")
                    termination = False
                    items = []
                    while not termination and not done:
                        # Select Action
                        chosen_item = rec_agent.select_action(state, cand["item"], action_space["item"])
                        items.append(chosen_item.item())

                        # Env Interaction
                        next_state, next_cand, action_space, reward, done = env.step(None, items, mode="train")

                        # Reward Collection
                        epi_reward += reward

                        # Whether Termination
                        next_state_emb = rec_agent.gcn_net([next_state])
                        term_score = rec_agent.termination_net(next_state_emb).item()
                        print("Termination Score:", term_score)
                        if term_score >= 0.5 or env.cur_conver_step > args.max_rec_step:
                            termination = True

                        # reward
                        if (termination or done) and t == env.max_turn and reward < 0:
                            reward += env.reward_dict["quit"]
                        reward_ = torch.tensor([reward], device=args.device, dtype=torch.float)

                        # Push memory
                        if done:
                            next_state = None
                        rec_agent.memory.push(state, torch.tensor(chosen_item).cpu(), next_state, reward_.cpu(),
                                              next_cand["item"], next_cand["feature"])
                        state = next_state
                        cand = next_cand

                        # After Done
                        if done or (termination and t == args.max_turn):
                            # every episode update the target model to be same with model
                            if reward == env.reward_dict["rec_acc"]:  # recommend successfully
                                Suc_Turn_list.append(t)
                                HDCG_item += calculate_hdcg_item(t, done)

                            AvgT_list.append(t)
                            total_reward += epi_reward
                            break
                    # Optimize
                    loss, loss_state = rec_agent.optimize_model(args.batch_size, args.gamma, ask_agent, args.term_reg)
                    if loss is not None:
                        rec_loss.append(loss)
                        rec_state_infer_loss.append(loss_state)

                    loss, loss_state = ask_agent.optimize_model(args.batch_size, args.gamma, rec_agent, args.term_reg)
                    if loss is not None:
                        ask_loss.append(loss)
                        ask_state_infer_loss.append(loss_state)

                if option == 1:
                    ask_step_list.append(env.cur_conver_step - 1)
                else:
                    rec_step_list.append(env.cur_conver_step - 1)

                env.cur_conver_turn += 1

        enablePrint()  # Enable print function

        # Analysis
        stl = np.array(Suc_Turn_list)
        SR5 = len(stl[stl <= 5]) / args.sample_times
        SR10 = len(stl[stl <= 10]) / args.sample_times
        SR15 = len(stl[stl <= 15]) / args.sample_times
        Avg_REC_Turn = len(rec_step_list) / args.sample_times
        Avg_ASK_Turn = len(ask_step_list) / args.sample_times
        Avg_Turn = statistics.mean(AvgT_list)
        Avg_REC_Step = statistics.mean(rec_step_list)
        Avg_ASK_Step = statistics.mean(ask_step_list)
        HDCG_attribute = statistics.mean(HDCG_attribute_list) if len(HDCG_attribute_list) else 0
        print('\nSample Times:{}'.format(args.sample_times))
        print('Recommend loss : {}'.format(statistics.mean(rec_loss)))
        print('Recommend State Infer loss : {}'.format(statistics.mean(rec_state_infer_loss)))
        print('Ask loss : {}'.format(statistics.mean(ask_loss)))
        print('Ask State Infer loss : {}'.format(statistics.mean(ask_state_infer_loss)))
        print('SR5:{}\nSR10:{}\nSR15:{}\nHDCG_item:{}\nHDCG_attribute:{}\nrewards:{}\n'.format(
            SR5, SR10, SR15, HDCG_item / args.sample_times, HDCG_attribute, total_reward / args.sample_times))
        print('Avg_Turn:{}\nAvg_REC_Turn:{}\nAvg_ASK_Turn:{}\nAvg_REC_STEP:{}\nAvg_ASK_STEP:{}'.format(
            Avg_Turn, Avg_REC_Turn, Avg_ASK_Turn, Avg_REC_Step, Avg_ASK_Step))

        results = [SR5, SR10, SR15, Avg_Turn, Avg_REC_Turn, Avg_ASK_Turn, Avg_REC_Step, Avg_ASK_Step, HDCG_item / args.sample_times]
        save_rl_mtric(args.data_name, 'Train-' + filename, epoch, results, time.time() - start, mode='train')
        if epoch % args.save_epoch_num == 0:
            ask_agent.save_model(data_name=args.data_name, filename=filename, epoch_user=epoch)
            rec_agent.save_model(data_name=args.data_name, filename=filename, epoch_user=epoch)
            value_net.save_value_net(data_name=args.data_name, filename=filename, epoch_user=epoch)
        if epoch % args.eval_epoch_num == 0:
            _ = rl_evaluate(args, kg, dataset, filename, epoch, ask_agent, rec_agent)
    # print(test_performance)


def set_arguments():
    parser = argparse.ArgumentParser()
    # Basic Setting
    parser.add_argument('--data_name', type=str, default=LAST_FM_STAR, choices=[LAST_FM_STAR, YELP_STAR, BOOK, MOVIE],
                        help='One of {LAST_FM_STAR, YELP_STAR, BOOK, MOVIE}.')
    parser.add_argument('--max_rec_step', type=int, default=10, help='max recommend step in one turn')
    parser.add_argument('--max_ask_step', type=int, default=3, help='max ask step in one turn')
    parser.add_argument('--cand_feature_num', type=int, default=10, help='candidate sampling number')
    parser.add_argument('--cand_item_num', type=int, default=10, help='candidate item sampling number')
    parser.add_argument('--max_turn', type=int, default=15, help='max conversation turn')
    
    # Epoch Setting
    parser.add_argument('--sample_times', type=int, default=100, help='the episodes of sampling')
    parser.add_argument('--max_epoch', type=int, default=100, help='max training epoch')
    parser.add_argument('--eval_epoch_num', type=int, default=5, help='the number of epoch to evaluate rl model and metric')
    parser.add_argument('--save_epoch_num', type=int, default=5, help='the number of epoch to save rl model and metric')
    
    # Evaluate Setting
    parser.add_argument('--eval_user_size', '-eval_user_size', type=int, default=100, help='user size of evaluation in training or testing.')
    parser.add_argument('--load_rl_epoch', type=int, default=0, help='the epoch of loading rl model')
    
    # GPU Resource Setting
    parser.add_argument('--block_print', '-block_print', type=int, default=1, help='Block Print or Not.')
    parser.add_argument('--seed', '-seed', type=int, default=1, help='random seed.')
    parser.add_argument('--gpu', type=str, default='0', help='gpu device.')
    
    # Hyperparameters Setting
    parser.add_argument('--batch_size', type=int, default=64, help='batch size.')
    parser.add_argument('--gamma', type=float, default=0.999, help='reward discount factor.')
    parser.add_argument('--learning_rate', type=float, default=1e-4, help='learning rate.')
    parser.add_argument('--l2_norm', type=float, default=1e-6, help='l2 regularization.')
    parser.add_argument('--alpha', type=float, default=1, help='TD alpha.')
    parser.add_argument('--hidden_size', type=int, default=100, help='number of samples')
    parser.add_argument('--memory_size', type=int, default=50000, help='size of memory ')
    parser.add_argument('--option_strategy', type=int, default=0, help='{"0": softmax, "1": max}')
    parser.add_argument('--term_reg', type=float, default=0, help='termination regularization')

    # Graph and Embedding
    parser.add_argument('--entropy_method', type=str, default='weight_entropy',
                        help='entropy_method is one of {entropy, weight entropy}')
    parser.add_argument('--fix_emb', action='store_false', help='fix embedding or not')
    parser.add_argument('--embed', type=str, default='transe', help='pretrained embeddings')
    parser.add_argument('--seq', type=str, default='transformer', help='sequential learning method')
    parser.add_argument('--gcn', action='store_false', help='use GCN or not')

    args = parser.parse_args()
    return args
