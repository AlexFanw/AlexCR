import statistics
from collections import namedtuple
from itertools import chain

import numpy as np
import torch.nn as nn
import torch.optim as optim

from rl.network.network_advantage import AdvantageNetwork
from rl.network.network_termination import TerminationNetwork
from rl.recommend_env.env_variable_question import VariableRecommendEnv
from utils.utils import *
from graph.gcn import StateTransitionProb
import warnings

warnings.filterwarnings("ignore")

RecommendEnv = {
    LAST_FM_STAR:  VariableRecommendEnv,
    YELP_STAR:  VariableRecommendEnv
}
FeatureDict = {
    LAST_FM_STAR: 'feature',
    YELP_STAR: 'feature',
    BOOK:'feature',
    MOVIE:'feature'
}

Transition = namedtuple('Transition',
                        ('state', 'action', 'next_state', 'reward', 'next_cand_items', 'next_cand_features'))


class AskAgent(object):
    def __init__(self, device, memory, action_size, hidden_size, gcn_net, learning_rate, l2_norm,
                 PADDING_ID, value_net, EPS_END=0.1, tau=0.01, alpha=1):
        self.EPS_END = EPS_END
        self.device = device
        self.alpha = alpha
        # GCN+Transformer Embedding
        self.gcn_net = gcn_net.to(device)
        # Termination Network
        self.termination_net = TerminationNetwork(hidden_size).to(device)
        # Value Network
        self.value_net = value_net
        # Action Select
        self.policy_net = AdvantageNetwork(action_size, hidden_size).to(device)
        self.target_net = AdvantageNetwork(action_size, hidden_size).to(device)
        self.target_net.load_state_dict(self.policy_net.state_dict())
        self.target_net.eval()
        # state_inferrer
        self.state_inferrer = StateTransitionProb(gcn=gcn_net, state_emb_size=hidden_size, cand_emb_size=action_size, device=device).to(device)
        # state optimizer
        self.optimizer_state = optim.Adam(chain(self.state_inferrer.parameters()),
                                          lr=learning_rate,
                                          weight_decay=l2_norm)
        # Optimizer
        self.optimizer = optim.Adam(chain(self.policy_net.parameters(),
                                          self.gcn_net.parameters(),
                                          self.value_net.parameters()),
                                    lr=learning_rate,
                                    weight_decay=l2_norm)
        # state optimizer
        self.optimizer_termination = optim.Adam(chain(self.termination_net.parameters()),
                                                lr=learning_rate,
                                                weight_decay=l2_norm)
        self.memory = memory
        self.loss_func = nn.MSELoss()
        self.PADDING_ID = PADDING_ID
        self.tau = tau

    def select_action(self, state, cand_features, features_space, is_test=False):
        state_emb = self.gcn_net([state])
        cand_features = torch.LongTensor([cand_features]).to(self.device)
        cand_emb = self.gcn_net.embedding(cand_features)
        sample = random.random()
        eps_threshold = self.EPS_END
        '''
        Greedy Soft Policy
        '''
        if is_test or sample > eps_threshold:
            with torch.no_grad():
                actions_value = self.policy_net(state_emb, cand_emb)
                chosen_feature = cand_features[0][actions_value.argmax().item()]
                return chosen_feature
        else:
            shuffled_cand = features_space
            random.shuffle(shuffled_cand)
            return torch.tensor(shuffled_cand[0], device=self.device, dtype=torch.long)

    def update_target_model(self):
        # soft assign
        for target_param, policy_param in zip(self.target_net.parameters(), self.policy_net.parameters()):
            target_param.data.copy_(self.tau * policy_param.data + target_param.data * (1.0 - self.tau))

    def optimize_model(self, BATCH_SIZE, GAMMA, rec_agent=None, term_reg=0):
        if len(self.memory) < BATCH_SIZE:
            return None, None

        self.update_target_model()

        idxs, transitions, is_weights = self.memory.sample(BATCH_SIZE)
        batch = Transition(*zip(*transitions))
        non_final_mask = torch.tensor(tuple(map(lambda s: s is not None, batch.next_state)), device=self.device, dtype=torch.uint8)

        n_states = []
        n_cand_features = []
        n_cand_items = []
        for s, ci, cf in zip(batch.next_state, batch.next_cand_items, batch.next_cand_features):
            if s is not None:
                n_states.append(s)
                n_cand_items.append(ci)
                n_cand_features.append(cf)
        if not n_states:
            return 0, 0

        '''
        Critic Loss. Termination Loss.
        '''
        next_state_emb_batch = self.gcn_net(n_states)
        state_emb_batch = self.gcn_net(list(batch.state))
        
        q_value = self.value_net(state_emb_batch)
        q_value_next = self.value_net(next_state_emb_batch)
        
        q_now_features, q_next_features = self.calculate_q_score(BATCH_SIZE, batch, next_state_emb_batch, n_cand_features, state_emb_batch)
        _, q_next_items = self.calculate_q_score(BATCH_SIZE, batch, next_state_emb_batch, n_cand_items, state_emb_batch, rec_agent)

        next_termination = self.termination_net(next_state_emb_batch)
        termination = self.termination_net(state_emb_batch)

        reward_batch = torch.FloatTensor(np.array(batch.reward).astype(float).reshape(-1, 1)).squeeze().to(self.device)

        q_cat = torch.cat((q_next_features.unsqueeze(0), q_next_items.unsqueeze(0)), dim=0)
        q_softmax_score = torch.softmax(q_cat, dim=0)
        q_softmax = torch.sum(torch.multiply(q_softmax_score, q_cat), dim=0)

        q_max, _ = torch.max(torch.cat((q_next_features.unsqueeze(0), q_next_items.unsqueeze(0))), dim=0)
        
        q_now_target = reward_batch
        q_estim = q_max
        # q_estim = q_value
        # q_estim = q_softmax
        q_now_target[non_final_mask] += GAMMA * ((1-next_termination) * q_next_features[non_final_mask]
                                                 + next_termination * q_estim[non_final_mask])
        # q_now_target = q_now_features + self.alpha * (q_now_target - q_now_features)

        # prioritized experience replay
        errors = (q_now_features - q_now_target).detach().cpu().squeeze().tolist()
        self.memory.update(idxs, errors)

        # Critic loss
        critic_loss = (torch.FloatTensor(is_weights).to(self.device) * self.loss_func(q_now_features, q_now_target.detach())).mean()
        # termination loss
        termination_loss = next_termination * (q_next_features[non_final_mask].detach() - q_value_next.detach() - term_reg)
        termination_loss = (torch.FloatTensor(is_weights).to(self.device)[non_final_mask] * termination_loss).mean()
        
        # update
        self.optimizer.zero_grad()
        self.optimizer_termination.zero_grad()

        critic_loss.backward()
        termination_loss.backward()

        self.optimizer.step()
        self.optimizer_termination.step()

        # state transition loss
        states = []
        actions = []
        rewards = []
        for s, a, r in zip(batch.state, batch.action, batch.reward):
            if s is not None:
                states.append(s)
                actions.append([a])
                if r <= 0:
                    rewards.append(torch.FloatTensor([0]))
                else:
                    rewards.append(torch.FloatTensor([1]))
        infer_reward = self.state_inferrer(states, torch.LongTensor(actions))
        transition_loss = (self.loss_func(infer_reward, torch.stack(rewards).to(self.device))).mean()
        self.optimizer_state.zero_grad()
        transition_loss.backward()
        self.optimizer_state.step()

        return critic_loss.data.item() + termination_loss.data.item(), transition_loss.data.item()

    def calculate_q_score(self, BATCH_SIZE, batch, next_state_emb_batch, n_cands, state_emb_batch, rec_agent=None):
        if rec_agent == None:
            action_batch = torch.LongTensor(np.array(batch.action).astype(int).reshape(-1, 1)).to(self.device)  # [N*1]

            action_emb_batch = self.gcn_net.embedding(action_batch)
            non_final_mask = torch.tensor(tuple(map(lambda s: s is not None,
                                                    batch.next_state)), device=self.device, dtype=torch.uint8)

            next_cand_batch = self.padding(n_cands)
            next_cand_emb_batch = self.gcn_net.embedding(next_cand_batch)
            q_now = self.policy_net(state_emb_batch, action_emb_batch, choose_action=False) + self.value_net(state_emb_batch)

            next_action_value = self.target_net(next_state_emb_batch, next_cand_emb_batch, choose_action=False)
            next_state_value = self.value_net(next_state_emb_batch)
            next_score = (next_state_value.unsqueeze(-1) + next_action_value).detach().cpu().numpy()
            next_exp = np.exp(next_score)
            next_sum = np.expand_dims(next_exp.sum(axis=1), axis=1)
            next_prop = next_exp / next_sum
            ask_Q = np.multiply(next_prop, next_score).sum(axis=1)
            q_next = torch.zeros((BATCH_SIZE), device=self.device)
            q_next[non_final_mask] = torch.FloatTensor(ask_Q).to(self.device)

            print("Q now:{}, Q next:{}, V next:{}".format(q_now[0], q_next[0], next_state_value[0]))
            return q_now, q_next.detach()
        else:
            action_batch = torch.LongTensor(np.array(batch.action).astype(int).reshape(-1, 1)).to(rec_agent.device)

            action_emb_batch = rec_agent.gcn_net.embedding(action_batch)
            non_final_mask = torch.tensor(tuple(map(lambda s: s is not None,
                                                    batch.next_state)), device=rec_agent.device, dtype=torch.uint8)

            next_cand_batch = rec_agent.padding(n_cands)
            next_cand_emb_batch = rec_agent.gcn_net.embedding(next_cand_batch)

            q_now = rec_agent.policy_net(state_emb_batch, action_emb_batch, choose_action=False) + rec_agent.value_net(state_emb_batch)

            next_action_value = rec_agent.target_net(next_state_emb_batch, next_cand_emb_batch, choose_action=False)
            next_state_value = rec_agent.value_net(next_state_emb_batch)
            next_score = (next_state_value.unsqueeze(-1) + next_action_value).detach().cpu().numpy()
            next_exp = np.exp(next_score)
            next_sum = np.expand_dims(next_exp.sum(axis=1), axis=1)
            next_prop = next_exp / next_sum
            rec_Q = np.multiply(next_prop, next_score).sum(axis=1)
            q_next = torch.zeros((BATCH_SIZE), device=rec_agent.device)
            q_next[non_final_mask] = torch.FloatTensor(rec_Q).to(rec_agent.device)

            print("Q now:{}, Q next:{}, V next:{}".format(q_now[0], q_next[0], next_state_value[0]))
            return q_now, q_next.detach()

    def save_model(self, data_name, filename, epoch_user):
        save_rl_agent(dataset=data_name,
                      model={'policy': self.policy_net.state_dict(),
                             'gcn': self.gcn_net.state_dict(),
                             'termination': self.termination_net.state_dict(),
                             'state': self.state_inferrer.state_dict()},
                      filename=filename, epoch_user=epoch_user, agent="ask")

    def load_model(self, data_name, filename, epoch_user):
        model_dict = load_rl_agent(dataset=data_name, filename=filename, epoch_user=epoch_user, agent="ask")
        self.policy_net.load_state_dict(model_dict['policy'])
        self.gcn_net.load_state_dict(model_dict['gcn'])
        self.termination_net.load_state_dict(model_dict['termination'])
        self.state_inferrer.load_state_dict(model_dict['state'])

    def padding(self, cand):
        pad_size = max([len(c) for c in cand])
        padded_cand = []
        for c in cand:
            cur_size = len(c)
            new_c = np.ones((pad_size)) * self.PADDING_ID
            new_c[:cur_size] = c
            padded_cand.append(new_c)
        return torch.LongTensor(np.array(padded_cand).astype(int)).to(self.device)