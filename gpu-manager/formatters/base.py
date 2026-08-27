import abc

class BaseFormatter(abc.ABC):
    @abc.abstractmethod
    def format_status(self, data: dict) -> str:
        pass

    @abc.abstractmethod
    def format_profiles(self, data: dict) -> str:
        pass

    @abc.abstractmethod
    def format_action_result(self, data: dict) -> str:
        pass
