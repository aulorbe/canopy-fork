import json
import os
from typing import Optional, Any, Dict, Union, Iterable, List

import jsonschema
import openai
from openai import AzureOpenAI

from canopy.llm import OpenAILLM
from canopy.llm.models import Function
from canopy.models.api_models import ChatResponse, StreamingChatChunk
from canopy.models.data_models import Messages, Query


class AzureOpenAILLM(OpenAILLM):
    """
    Azure OpenAI LLM wrapper built on top of the OpenAI Python client.

    Note: Azure OpenAI services requires an Azure API key, Azure endpoint, and OpenAI API version.

    a valid Azure API key and a valid Azure endpoint to use this class.
          You can set the "AZURE_OPENAI_KEY" and "AZURE_OPENAI_ENDPOINT" environment variables to your API key and
          endpoint, respectively, or you can directly, e.g.:
          >>> from openai import AzureOpenAI
          >>> AzureOpenAI.api_key = "YOUR_AZURE_API_KEY"
          >>> AzureOpenAI.api_base = "YOUR_AZURE_ENDPOINT"
          >>> AzureOpenAI.api_version = "THE_AZURE_API_VERSION_YOU_ARE_USING"

    Note: If you want to pass an OpenAI organization, you need to set an environment variable "OPENAI_ORG_ID". Note
          that this is different from the environment variable name for passing an organization to the parent class,
          OpenAILLM, which is "OPENAI_ORG".

          You cannot currently set this environment variable manually, as shown above.
    """
    def __init__(self,
                 model_name: str = "gpt-3.5-turbo",  # why do i need this?
                 *,
                 azure_deployment: Optional[str] = None,
                 azure_endpoint: Optional[str] = None,
                 azure_api_version: Optional[str] = None,
                 azure_api_key: Optional[str] = None,
                 **kwargs: Any,
                 ):
        """
        Initialize the AzureOpenAI LLM.

        >>> os.environ['OPENAI_API_VERSION'] = "OPENAI API VERSION (FOUND IN AZURE RESTAPI DOCS)"
        >>> os.environ['AZURE_OPENAI_ENDPOINT'] = "AZURE ENDPOINT"
        >>> os.environ['AZURE_OPENAI_API_KEY'] = "YOUR KEY"
        >>> os.environ['AZURE_DEPLOYMENT'] = "YOUR AZURE DEPLOYMENT'S NAME"

        >>> from canopy.models.data_models import UserMessage
        >>> llm = AzureOpenAILLM()
        >>> messages = [UserMessage(content="Hello! How are you?")]
        >>> llm.chat_completion(messages)

        """
        super().__init__(model_name)

        if os.environ['AZURE_OPENAI_API_KEY'] is None:
            raise EnvironmentError('Please set your Azure OpenAI API key environment variable ('
                                   'export AZURE_OPENAI_API_KEY=<your azure openai api key>). See here for more '
                                   'information: '
                                   'https://learn.microsoft.com/en-us/azure/ai-services/openai/quickstart?tabs=command-line%2Cpython&pivots=programming-language-python')

        if os.environ['AZURE_OPENAI_ENDPOINT'] is None:
            raise EnvironmentError("Please set your Azure OpenAI endpoint environment variable ('export "
                                   "AZURE_OPENAI_ENDPOINT=<your endpoint>'). See here for more information "
                                   "https://learn.microsoft.com/en-us/azure/ai-services/openai/quickstart?tabs=command-line%2Cpython&pivots=programming-language-python")

        if os.environ['OPENAI_API_VERSION'] is None:
            raise EnvironmentError("Please set your Azure OpenAI API version. ('export OPENAI_API_VERSION=<your API "
                                   "version"
                                   ">'). See here for more information "
                                   "https://learn.microsoft.com/en-us/azure/ai-services/openai/quickstart?tabs=command-line%2Cpython&pivots=programming-language-python")

        if azure_deployment is None:
            if os.getenv('AZURE_DEPLOYMENT') is None:
                raise EnvironmentError('You need to set an environment variable for AZURE_DEPLOYMENT to the name of '
                                       'your Azure deployment')
            azure_deployment = os.getenv('AZURE_DEPLOYMENT')

        openai.api_type = "azure"

        self._client = AzureOpenAI(azure_deployment=azure_deployment,
                                   azure_endpoint=azure_endpoint,
                                   api_version=azure_api_version,
                                   api_key=azure_api_key,

                       )
        self.default_model_params = kwargs

    def enforced_function_call(self,
                               messages: Messages,
                               function: Function,
                               *,
                               max_tokens: Optional[int] = None,
                               model_params: Optional[Dict] = None) -> dict:
        """
        This function enforces the model to respond with a specific function call.

        To read more about this feature, see: https://platform.openai.com/docs/guides/gpt/function-calling

        Note: this function is wrapped in a retry decorator to handle transient errors.

        Args:
            messages: Messages (chat history) to send to the model.
            function: Function to call. See canopy.llm.models.Function for more details.
            max_tokens: Maximum number of tokens to generate. Defaults to None (generates until stop sequence or until hitting max context size).
            model_params: Model parameters to use for this request. Defaults to None (uses the default model parameters).
                            see: https://platform.openai.com/docs/api-reference/chat/create

        Returns:
            dict: Function call arguments as a dictionary.

        Usage:
            >>> from canopy.llm import OpenAILLM
            >>> from canopy.llm.models import Function, FunctionParameters, FunctionArrayProperty
            >>> from canopy.models.data_models import UserMessage
            >>> llm = OpenAILLM()
            >>> messages = [UserMessage(content="I was wondering what is the capital of France?")]
            >>> function = Function(
            ...     name="query_knowledgebase",
            ...     description="Query search engine for relevant information",
            ...     parameters=FunctionParameters(
            ...         required_properties=[
            ...             FunctionArrayProperty(
            ...                 name="queries",
            ...                 items_type="string",
            ...                 description='List of queries to send to the search engine.',
            ...             ),
            ...         ]
            ...     )
            ... )
            >>> llm.enforced_function_call(messages, function)
            {'queries': ['capital of France']}
        """  # noqa: E501
        # this enforces the model to call the function
        function_call = {"name": function.name}

        model_params_dict: Dict[str, Any] = {}
        model_params_dict.update(
            **self.default_model_params
        )
        if model_params:
            model_params_dict.update(**model_params)

        messages = [m.dict() for m in messages]

        chat_completion = self._client.chat.completions.create(  # used to be ChatCompletions
            model=self.model_name,
            messages=messages,
            functions=[function.dict()],
            function_call=function_call,
            max_tokens=max_tokens,
            **model_params_dict
        )

        result = chat_completion.choices[0].message.function_call
        arguments = json.loads(result.arguments)

        jsonschema.validate(instance=arguments, schema=function.parameters.dict())
        return arguments

    async def achat_completion(self,
                               messages: Messages, *, stream: bool = False,
                               max_generated_tokens: Optional[int] = None,
                               model_params: Optional[Dict] = None
                               ) -> Union[ChatResponse,
    Iterable[StreamingChatChunk]]:
        raise NotImplementedError()

    async def agenerate_queries(self,
                                messages: Messages,
                                *,
                                max_generated_tokens: Optional[int] = None,
                                model_params: Optional[Dict] = None
                                ) -> List[Query]:
        raise NotImplementedError()
