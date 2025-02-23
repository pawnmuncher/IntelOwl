# This file is a part of IntelOwl https://github.com/intelowlproject/IntelOwl
# See the file 'LICENSE' for copying permission.
import abc
import logging
from typing import Optional, Type

import requests
from django.conf import settings

from certego_saas.apps.user.models import User

from ..choices import PythonModuleBasePaths, ReportStatus
from ..classes import Plugin
from .exceptions import ConnectorConfigurationException, ConnectorRunException
from .models import ConnectorConfig, ConnectorReport

logger = logging.getLogger(__name__)


class Connector(Plugin, metaclass=abc.ABCMeta):
    """
    Abstract class for all Connectors.
    Inherit from this branch when defining a connector.
    Need to overrwrite `set_params(self, params: dict)`
     and `run(self)` functions.
    """

    @classmethod
    @property
    def python_base_path(cls):
        return PythonModuleBasePaths.Connector.value

    @classmethod
    @property
    def report_model(cls) -> Type[ConnectorReport]:
        return ConnectorReport

    @classmethod
    @property
    def config_model(cls) -> Type[ConnectorConfig]:
        return ConnectorConfig

    def get_exceptions_to_catch(self) -> list:
        return [
            ConnectorConfigurationException,
            ConnectorRunException,
        ]

    def before_run(self):
        super().before_run()
        logger.info(f"STARTED connector: {self.__repr__()}")
        self._config: ConnectorConfig
        # an analyzer can start
        # if the run_on_failure flag is set
        # if there are no analyzer_reports
        # it all the analyzer_reports are not failed
        if (
            self._config.run_on_failure
            or not self._job.analyzerreports.count()
            or self._job.analyzerreports.exclude(
                status=ReportStatus.FAILED.value
            ).exists()
        ):
            logger.info(
                f"Running connector {self.__class__.__name__} "
                f"even if job status is {self._job.status} because"
                "run on failure is set"
            )
        else:
            raise ConnectorRunException(
                "An analyzer has failed,"
                f" unable to run connector {self.__class__.__name__}"
            )

    def after_run(self):
        super().after_run()
        logger.info(f"FINISHED connector: {self.__repr__()}")

    @classmethod
    def health_check(cls, connector_name: str, user: User) -> Optional[bool]:
        """
        basic health check: if instance is up or not (timeout - 10s)
        """
        ccs = cls.config_model.objects.filter(name=connector_name)
        if not ccs.count():
            raise ConnectorRunException(f"Unable to find connector {connector_name}")
        for cc in ccs:
            logger.info(f"Found connector runnable {cc.name} for user {user.username}")
            for param in (
                cc.parameters.filter(name__startswith="url")
                .annotate_configured(cc, user)
                .annotate_value_for_user(cc, user)
            ):
                if not param.configured or not param.value:
                    continue
                url = param.value
                logger.info(
                    f"Url retrieved to verify is {param.name} for connector {cc.name}"
                )

                if url.startswith("http"):
                    logger.info(f"Checking url {url} for connector {cc.name}")
                    if settings.STAGE_CI or settings.MOCK_CONNECTIONS:
                        return True
                    try:
                        # momentarily set this to False to
                        # avoid fails for https services
                        requests.head(url, timeout=10, verify=False)
                    except (
                        requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout,
                    ) as e:
                        logger.info(
                            f"Health check failed: url {url}"
                            f" for connector {cc.name}. Error: {e}"
                        )
                        health_status = False
                    else:
                        health_status = True

                    return health_status
        raise ConnectorRunException(
            f"Unable to find configured connector {connector_name}"
        )
