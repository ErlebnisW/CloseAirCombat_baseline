import numpy as np
from collections import OrderedDict
from gym import spaces
from torch import ones, pdist
from .task_base import BaseTask
from ..core.catalog import Catalog as c
from ..reward_functions import AltitudeReward, PostureReward, RelativeAltitudeReward
from ..termination_conditions import ExtremeState, LowAltitude, Overload, ShootDown, Timeout
from ..utils.utils import lonlat2dis, get_AO_TA_R


class SingleCombatTask(BaseTask):
    def __init__(self, config):
        self.config = config
        self.num_agents = getattr(self.config, 'num_agents', 2)
        assert self.num_agents == 2, 'Only support one-to-one fighter combat!'

        self.reward_functions = [
            AltitudeReward(self.config),
            PostureReward(self.config),
            RelativeAltitudeReward(self.config),
        ]

        self.termination_conditions = [
            ExtremeState(self.config),
            Overload(self.config),
            LowAltitude(self.config),
            Timeout(self.config),
        ]
        self.load_variables()
        self.load_observation_space()
        self.load_action_space()

    def load_variables(self):
        self.state_var = [
            c.position_long_gc_deg,             #  0. lontitude (unit: degree)
            c.position_lat_geod_deg,            #  1. latitude  (unit: degree)
            c.position_h_sl_ft,                 #  2. altitude  (unit: feet)
            c.attitude_roll_rad,                #  3. roll      (unit: rad)
            c.attitude_pitch_rad,               #  4. pitch     (unit: rad)
            c.attitude_heading_true_rad,        #  5. yaw       (unit: rad)
            c.velocities_v_north_fps,           #  6. v_north   (unit: fps)
            c.velocities_v_east_fps,            #  7. v_east    (unit: fps)
            c.velocities_v_down_fps,            #  8. v_down    (unit: fps)
            c.velocities_vc_fps,                #  9. vc        (unit: fps)
            c.accelerations_n_pilot_x_norm,     # 10. a_north   (unit: G)
            c.accelerations_n_pilot_y_norm,     # 11. a_east    (unit: G)
            c.accelerations_n_pilot_z_norm,     # 12. a_down    (unit: G)
        ]
        self.action_var = [
            c.fcs_aileron_cmd_norm,             # [-1., 1.]
            c.fcs_elevator_cmd_norm,            # [-1., 1.]
            c.fcs_rudder_cmd_norm,              # [-1., 1.]
            c.fcs_throttle_cmd_norm,            # [0.4, 0.9]
        ]
        self.render_var = [
            c.position_long_gc_deg,
            c.position_lat_geod_deg,
            c.position_h_sl_ft,
            c.attitude_roll_rad,
            c.attitude_pitch_rad,
            c.attitude_heading_true_rad,
        ]

    def load_observation_space(self):
        self.observation_space = [spaces.Box(low=-10, high=10., shape=(18,)) for _ in range(self.num_agents)]

    def load_action_space(self):
        # aileron, elevator, rudder, throttle
        self.action_space = [spaces.MultiDiscrete([41, 41, 41, 30]) for _ in range(self.num_agents)]

    def normalize_observation(self, env, observations):
        """Convert simulation states into the format of observation_space
        """
        def _normalize(agent_id):
            ego_idx, enm_idx = agent_id, (agent_id + 1) % self.num_agents
            ego_obs_list, enm_obs_list = observations[ego_idx], observations[enm_idx]
            # (0) extract feature: [north(km), east(km), down(km), v_n(mh), v_e(mh), v_d(mh)]
            ego_cur_east, ego_cur_north = lonlat2dis(ego_obs_list[0], ego_obs_list[1], env.init_longitude, env.init_latitude)
            enm_cur_east, enm_cur_north = lonlat2dis(enm_obs_list[0], enm_obs_list[1], env.init_longitude, env.init_latitude)
            ego_feature = np.array([
                ego_cur_north / 1000, ego_cur_east / 1000, ego_obs_list[2] * 0.304 / 1000,
                ego_obs_list[6] * 0.304 / 340, ego_obs_list[7] * 0.304 / 340, ego_obs_list[8] * 0.304 / 340
            ])
            enm_feature = np.array([
                enm_cur_north / 1000, enm_cur_east / 1000, enm_obs_list[2] * 0.304 / 1000,
                enm_obs_list[6] * 0.304 / 340, enm_obs_list[7] * 0.304 / 340, enm_obs_list[8] * 0.304 / 340
            ])
            observation = np.zeros(18)
            # (1) ego info normalization
            observation[0] = ego_obs_list[2] * 0.304 / 5000     #  0. ego altitude  (unit: 5km)
            observation[1] = np.linalg.norm(ego_feature[3:])    #  1. ego_v         (unit: mh)
            observation[2] = ego_obs_list[8]                    #  2. ego_v_down    (unit: mh)
            observation[3] = np.sin(ego_obs_list[3])            #  3. ego_roll_sin
            observation[4] = np.cos(ego_obs_list[3])            #  4. ego_roll_cos
            observation[5] = np.sin(ego_obs_list[4])            #  5. ego_pitch_sin
            observation[6] = np.cos(ego_obs_list[4])            #  6. ego_pitch_cos
            observation[7] = ego_obs_list[9] * 0.304 / 340      #  7. ego_vc        (unit: mh)
            observation[8] = ego_obs_list[10]                   #  8. ego_north_ng  (unit: 5G)
            observation[9] = ego_obs_list[11]                   #  9. ego_east_ng   (unit: 5G)
            observation[10] = ego_obs_list[12]                  # 10. ego_down_ng   (unit: 5G)
            # (2) relative info w.r.t enm state
            ego_AO, ego_TA, R, side_flag = get_AO_TA_R(ego_feature, enm_feature, return_side=True)
            observation[11] = R / 10                            # 11. relative distance (unit: 10km)
            observation[12] = ego_AO                            # 12. ego_AO        (unit: rad)
            observation[13] = ego_TA                            # 13. ego_TA        (unit: rad)
            observation[14] = side_flag                         # 14. enm_delta_heading: 1 or 0 or -1
            observation[15] = enm_obs_list[2] * 0.304 / 5000    # 15. enm_altitude  (unit: 5km)
            observation[16] = np.linalg.norm(enm_feature[3:])   # 16. enm_v         (unit: mh)
            observation[17] = enm_obs_list[8]                   # 17. enm_v_down    (unit: mh)
            return observation

        norm_obs = np.zeros((self.num_agents, 18))
        for agent_id in range(self.num_agents):
            norm_obs[agent_id] = _normalize(agent_id)
        return norm_obs

    def normalize_action(self, env, actions):
        """Convert discrete action index into continuous value.
        """
        def _normalize(agent_id):
            action = np.zeros(4)
            action[0] = actions[agent_id][0] * 2. / (self.action_space[0].nvec[0] - 1.) - 1.
            action[1] = actions[agent_id][1] * 2. / (self.action_space[0].nvec[1] - 1.) - 1.
            action[2] = actions[agent_id][2] * 2. / (self.action_space[0].nvec[2] - 1.) - 1.
            action[3] = actions[agent_id][3] * 0.5 / (self.action_space[0].nvec[3] - 1.) + 0.4
            return action

        norm_act = np.zeros((self.num_agents, 4))
        for agent_id in range(self.num_agents):
            norm_act[agent_id] = _normalize(agent_id)
        return norm_act

    def reset(self, env):
        """Task-specific reset, include reward function reset.

        Must call it after `env.get_observation()`
        """
        return super().reset(env)

    def get_reward(self, env, agent_id, info={}):
        """
        Must call it after `env.get_observation()`
        """
        return super().get_reward(env, agent_id, info)

    def get_termination(self, env, agent_id, info={}):
        return super().get_termination(env, agent_id, info)
