import numpy as np
from numpy.core.numeric import array_equal
from numpy.lib.arraypad import pad
from .reward_function_base import BaseRewardFunction
from ..utils.utils import get_AO_TA_R, lonlat2dis
from ..core.catalog import Catalog


class PostureReward(BaseRewardFunction):
    """
    PostureReward = Orientation * Range
    - Orientation: Encourage pointing at enemy fighter, punish when is pointed at.
    - Range: Encourage getting closer to enemy fighter, punish if too far away.

    NOTE:
    - Only support one-to-one environments.
    """
    def __init__(self, config):
        super().__init__(config)
        assert self.num_agents == 2, \
            "PostureReward only support one-to-one environments but current env has more than 2 agents!"
        self.orientation_version = getattr(self.config, f'{self.__class__.__name__}_orientation_version', 'v2')
        self.range_version = getattr(self.config, f'{self.__class__.__name__}_range_version', 'v2')
        self.target_dist = getattr(self.config, f'{self.__class__.__name__}_target_dist', 3.0)

        self.orientation_fn = self.get_orientation_function(self.orientation_version)
        self.range_fn = self.get_range_funtion(self.range_version)
        self.reward_item_names = [self.__class__.__name__ + item for item in ['', '_orn', '_range']]

    def get_reward(self, task, env, agent_id):
        """
        Reward is a complex function of AO, TA and R in the last timestep.

        Args:
            task: task instance
            env: environment instance

        Returns:
            (float): reward
        """
        ego_idx, enm_idx = agent_id, (agent_id + 1) % self.num_agents
        # feature: (north, east, down, vn, ve, vd) unit: km, mh
        ego_feature = np.hstack([env.sims[ego_idx].get_position() / 1000, env.sims[ego_idx].get_velocity() / 340])
        enm_feature = np.hstack([env.sims[enm_idx].get_position() / 1000, env.sims[enm_idx].get_velocity() / 340])
        AO, TA, R = get_AO_TA_R(ego_feature, enm_feature)
        orientation_reward = self.orientation_fn(AO, TA)
        range_reward = self.range_fn(R)
        new_reward = orientation_reward * range_reward
        return self._process(new_reward, agent_id, (orientation_reward, range_reward))

    def get_orientation_function(self, version):
        if version == 'v0':
            return lambda AO, TA: (1. - np.tanh(9 * (AO - np.pi / 9))) / 3. + 1 / 3. \
                + min((np.arctanh(1. - max(2 * TA / np.pi, 1e-4))) / (2 * np.pi), 0.) + 0.5
        elif version == 'v1':
            return lambda AO, TA: (1. - np.tanh(2 * (AO - np.pi / 2))) / 2. \
                * (np.arctanh(1. - max(2 * TA / np.pi, 1e-4))) / (2 * np.pi) + 0.5
        elif version == 'v2':
            return lambda AO, TA: 1 / (50 * AO / np.pi + 2) + 1 / 2 \
                + min((np.arctanh(1. - max(2 * TA / np.pi, 1e-4))) / (2 * np.pi), 0.) + 0.5
        else:
            raise NotImplementedError(f"Unknown orientation function version: {version}")

    def get_range_funtion(self, version):
        if version == 'v0':
            return lambda R: np.exp(-(R - self.target_dist) ** 2 * 0.004) / (1. + np.exp(-(R - self.target_dist + 2) * 2))
        elif version == 'v1':
            return lambda R: np.clip(1.2 * np.min([np.exp(-(R - self.target_dist) * 0.21), 1]) / \
                (1. + np.exp(-(R - self.target_dist + 1) * 0.8)), 0.3, 1)
        elif version == 'v2':
            return lambda R: max(np.clip(1.2 * np.min([np.exp(-(R - self.target_dist) * 0.21), 1]) / \
                (1. + np.exp(-(R - self.target_dist + 1) * 0.8)), 0.3, 1), np.sign(7 - R))
        else:
            raise NotImplementedError(f"Unknown range function version: {version}")
