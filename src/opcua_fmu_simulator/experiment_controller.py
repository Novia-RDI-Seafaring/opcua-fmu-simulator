import asyncio
from .server_setup_dev import OPCUAFMUServerSetup
from .config_loader import DataLoaderClass
from asyncua import Client, ua
import os
from pathlib import Path
from colorama import Fore, Style
from .operations import ops
from decimal import getcontext
import logging
from .connections import parse_connections # Connection
import time
from time import gmtime, strftime
logging.basicConfig(level=logging.ERROR) # required to get messages printed out
logger = logging.getLogger(__name__)
import re
from .infra.servers import server_manager
from .infra.clients import client_manager

getcontext().prec = 8
DEFAULT_BASE_PORT = 7000 # port from which the server initialization begins
DEFAULT_LOGGER_HEADER = "experiment_name, evaluation_name, evaluation_function, measured_value, experiment_result, system_timestamp\n"

class ExperimentSystem:
    def __init__(self, experiment_configs: list[str]) -> None:
        self.experiment_configs = experiment_configs
        self.log_file    = self.generate_logfile()
        self.config      = None 
        self.fmu_files   = None
        self.experiment  = None
        self.save_logs   = None
        self.timing      = None
        self.connections = None # description of system loop definition from experiment
        self.server_obj  = None
        self.reading_condition_dict  = {}
        self.evaluation_equation_dic = {}
        self.system_description      = {}
        self.system_node_ids         = {} # this is meant to take in all of the systems node id's
        self.regex_parser_pattern    = r'\d+\.\d+|\d+|[a-zA-Z_][\w]*|[<>!=]=?|==|!=|[^\s\w\.]'

    def generate_logfile(self):
        if not os.path.exists("logs"):
            os.makedirs("logs")
        file_path = os.path.join("logs", strftime("%Y_%m_%d_%H_%M_%S", gmtime()))
        if not os.path.exists(file_path):
            with open(file_path, 'w') as file:
                print(DEFAULT_LOGGER_HEADER)
                file.write(DEFAULT_LOGGER_HEADER)
        return file_path    
    
    def log_result(self, criterea, measured_value, evaluation_result, simulation_time):
        system_output = f"{self.config['experiment']['experiment_name']},\
            {criterea},\
            {self.evaluation_equation_dic[criterea]['target_obj']}.{self.evaluation_equation_dic[criterea]['target_var']} {self.evaluation_equation_dic[criterea]['operator']} {self.evaluation_equation_dic[criterea]['value']},\
            {measured_value},\
            {evaluation_result},\
            {simulation_time}\n"
        self.log_system_output(output= system_output)
    
        
    ########### SETTERS & GETTERS ########### 
    async def get_value(self, client_name: str, variable: ua.NodeId) -> None:
        client = self.client_obj.fetch_appropriacte_client(client_name=client_name)
        node = client.get_node(variable)
        return await node.read_value() 

    async def write_value(self, client_name:str, variable:str, value:str)->None:
        """
            write value to specific node in the system
            clienet_name = client to desired server
        """
        # if it's part of the systems servers
        if (client_name in self.server_obj.system_servers):
                object_node = self.client_obj.system_clients[client_name].get_node(ua.NodeId(1, 1))
                update_values = {
                    "variable": variable,
                    "value": value
                }
                await object_node.call_method(ua.NodeId(1, 3), str(update_values)) # update fmu before updating values
        
        # if it's an external server
        else:
            node_id = self.client_obj.system_node_ids[client_name][variable]
            client = self.client_obj.fetch_appropriacte_client(client_name=client_name)
            node = client.get_node(node_id)
            datavalue1 = await node.read_data_value()
            variant1 = datavalue1.Value
            await node.write_value(ua.DataValue(ua.Variant(value, variant1.VariantType)))            
            
    async def run_system_updates(self, timestep):
        for key in self.client_obj.system_clients.keys():
            client = self.client_obj.system_clients[key]
            object_node = client.get_node(ua.NodeId(1, 1))
            await object_node.call_method(ua.NodeId(1, 2), str(float(timestep)))
        return
    
    ################### SYSTEM UPDATES ########################
    async def run_single_loop(self):
        """
        update loop, passing outputs from one fmu to another
        1) Updates fmu1 to get the most recent values
        2) For every I/O perform the value transfer

        *NOTE: the function performs transfer and updates both FMU and Server variable
        ________                      ________
        |      |*OUTPUT1 ====> INPUT1*|      |
        | fmu1 |*OUTPUT2 ====> INPUT2*| fmu2 |
        |______|*OUTPUT3 ====> INPUT3*|______|
        """
        for update in self.connections:
            # get nodeid of the source variable
            value_nodid = self.system_node_ids[update.from_fmu][update.from_var]
            # get the value using the nodeid
            value = await self.get_value(client_name= update.from_fmu, variable= value_nodid)

            # write it to the targer value
            await self.write_value(
                client_name = update.to_fmu,
                variable    = update.to_var,
                value       = value
            )
            
            logger.info(f"\n\n passed fmu {update.from_fmu} var {update.from_var} with {value}, to fmu {update.to_fmu} var {update.to_var} \n\n")


    async def check_reading_conditions(self, conditions):
        """
        checks that the conditions required to start readings are met
        """
        for condition in self.reading_condition_dict:
            node = self.system_node_ids[self.reading_condition_dict[condition]["target_obj"]][self.reading_condition_dict[condition]["target_var"]]
            measured_value = self.client_obj.system_clients[self.reading_condition_dict[condition]["target_obj"]].get_node(node)
            measured_value = await measured_value.read_value()
            eval_criterea = self.reading_condition_dict[condition]["value"] 
            op            = self.reading_condition_dict[condition]["operator"]
            result        = ops[op](measured_value, eval_criterea) 
            variable      = self.reading_condition_dict[condition]["target_var"]

            if result:  logger.info(Fore.GREEN + f"condition  {variable} {op} {eval_criterea}  PASSED \nwith value: {measured_value}")
            else:       logger.info(Fore.RED + f"condition {variable} {op} {eval_criterea} FAILED \nwith value: {measured_value}")

            return result
        
    ################################################
    ############### SYSTEM TESTS ###################
    ################################################
    async def run_multi_step_experiment(self, experiment: dict):
        """
        Executes the experiment while regulating time according to experiment["timing"]:
        - "simulation_time": advances time instantly
        - "real_time": waits so each step aligns with real wall-clock time
        """
        sim_time = 0.0
        simulation_status = True
        # TODO: PUT IN SETUP
        timestep = float(experiment["timestep"])  # assumed constant across system

        if timestep > experiment["stop_time"]:
            raise ValueError("stop_time has to be equal or greater than step_time")

        print(f"""Starting simulation:
        Experiment: {self.experiment['experiment_name']}
        FMU's: {self.fmu_files}
        Simulating""", end="", flush=True)

        while simulation_status:
            start_wall_time = time.time()

            # Update all FMUs with one timestep into the future
            await self.run_system_updates(timestep=timestep)

            # Pass data between FMUs
            await self.run_single_loop()

            # Evaluation logic
            if await self.check_reading_conditions(experiment["start_readings_conditions"]):
                await self.check_outputs(experiment["evaluation"], simulation_time=sim_time)

            # Time advancement
            sim_time += timestep

            if self.timing == "real_time":
                await self.regulate_timestep(start_time= start_wall_time, timestep= timestep)
            
            print(".", end="", flush=True)

            if sim_time > experiment["stop_time"]:
                simulation_status = False
                print("Simulation ended\n\n ")

    async def regulate_timestep(self, start_time: float, timestep: float):
        elapsed = time.time() - start_time
        sleep_duration = timestep - elapsed
        if sleep_duration > 0:
            await asyncio.sleep(sleep_duration)
        elif sleep_duration < 0:
            logger.error("DURANTION OF LOOP EXCEEDS TIMESTEP!")
            # ADD MESSAGE TO LOG FILES
            
            
    async def run_experiment(self) -> None:
        """
        check_experiment_type
        call corresponding experiment
        """
        # reset and initialize system variables for every experiment
        await self.client_obj.reset_system() 
        await self.client_obj.initialize_system_variables(experiment=self.experiment)
        # parses system_loop section of the experiment and stores it to use it as the system loop
        print("Parsing connections...") 
        self.connections = parse_connections(self.experiment["system_loop"])
        await self.run_multi_step_experiment(experiment=self.experiment)

    def log_system_output(self, output):
        with open(self.log_file, 'a') as file:            
            file.write(output)

    #######################################################################
    ################   CHECK SYSTEM OUTPUTS   #############################
    #######################################################################
    async def check_outputs(self, evaluation: dict[list[dict]], simulation_time) -> None:
        """
        evaluation of system outputs, this function reads the "evaluation" section of the yaml file
        """
        for criterea in self.evaluation_equation_dic:
            node = self.system_node_ids[self.evaluation_equation_dic[criterea]["target_obj"]][self.evaluation_equation_dic[criterea]["target_var"]]
            measured_value = self.client_obj.system_clients[self.evaluation_equation_dic[criterea]["target_obj"]].get_node(node)
            measured_value = await measured_value.read_value()
            target_value = self.evaluation_equation_dic[criterea]["value"]
            op = self.evaluation_equation_dic[criterea]["operator"] 
            
            # compare the two values
            evaluation_result = ops[op](measured_value, target_value)
            variable = self.evaluation_equation_dic[criterea]["target_var"]

            if evaluation_result: logger.info(Fore.GREEN + f"experiment {variable} {op} {self.evaluation_equation_dic[criterea]['value']} = {evaluation_result} \n PASSED with value: {measured_value}")
            else:                 logger.info(Fore.RED   + f"experiment {variable} {op} {self.evaluation_equation_dic[criterea]['value']} = {evaluation_result} \n FAILED with value: {measured_value}")
            
            if self.save_logs:
                self.log_result(criterea          = criterea, 
                                measured_value    = measured_value, 
                                evaluation_result = evaluation_result, 
                                simulation_time   = simulation_time)
            
            logger.info(Style.RESET_ALL)


    ###########################################################################
    #####################   INIT SYSTEM IDS   #################################
    ###########################################################################
    def gather_system_ids(self):
        for server_name in self.server_obj.system_servers:
            self.system_node_ids[server_name] = self.server_obj.system_servers[server_name].server_variable_ids

    
    def _parse_conditions(self, conditions_dict, store_dict_name, description=""):
        """
        Generic parser for reading or evaluation conditions.
        
        conditions_dict: dict of {name: "FMU.variable operator value"}
        store_dict_name: string, attribute name to store parsed data
        """
        print(f"Parsing {description}s...")
        if not isinstance(conditions_dict, dict):
            raise ValueError(f"{description.capitalize()}s must be a dictionary")

        parsed_dict = {}

        for condition_name, cond_str in conditions_dict.items():
            try:
                match = re.findall(self.regex_parser_pattern, cond_str)
                if not match or len(match) != 4:
                    raise ValueError(f"{description.capitalize()} '{condition_name}' does not match expected pattern")

                target_obj, target_var, operator, value_str = match

                # Convert value to float
                try:
                    value = float(value_str)
                except ValueError:
                    raise ValueError(f"{description.capitalize()} '{condition_name}' has invalid numeric value: '{value_str}'")

                parsed_dict[condition_name] = {
                    "target_obj": target_obj,
                    "target_var": target_var,
                    "operator": operator,
                    "value": value
                }

            except Exception as e:
                print(f"Error parsing {description} '{condition_name}': {e}")

        setattr(self, store_dict_name, parsed_dict)


    async def initialize_experiment_params(self, experiment):
            print("Initializing experiment parameters...")
            self.config    = DataLoaderClass(experiment).data

            try:
                # Check FMU files
                self.fmu_files = self.config.get("fmu_files")
                if not isinstance(self.fmu_files, list) or not self.fmu_files:
                    raise ValueError("'fmu_files' must be a non-empty list of FMU paths")

                # Check external servers
                self.external_servers = self.config.get("external_servers", [])
                if not isinstance(self.external_servers, list):
                    raise ValueError("'external_servers' must be a list")

                # Check experiment section
                self.experiment = self.config.get("experiment")
                if not isinstance(self.experiment, dict):
                    raise ValueError("'experiment' section must be a dictionary")

                # Individual experiment parameters
                self.experiment_name = self.experiment.get("experiment_name")
                if not isinstance(self.experiment_name, str) or not self.experiment_name:
                    raise ValueError("'experiment_name' must be a non-empty string")

                self.timestep = self.experiment.get("timestep")
                if not isinstance(self.timestep, (int, float)) or self.timestep <= 0:
                    raise ValueError("'timestep' must be a positive number")

                self.timing = self.experiment.get("timing")
                if self.timing not in ["simulation_time", "real_time"]:
                    raise ValueError("'timing' must be either 'simulation_time' or 'real_time'")

                self.stop_time = self.experiment.get("stop_time")
                if not isinstance(self.stop_time, (int, float)) or self.stop_time <= 0:
                    raise ValueError("'stop_time' must be a positive number")

                self.save_logs = self.experiment.get("save_logs")
                if not isinstance(self.save_logs, bool):
                    raise ValueError("'save_logs' must be True or False")

                # Check initial system state
                self.initial_system_state = self.experiment.get("initial_system_state", {})
                if not isinstance(self.initial_system_state, dict):
                    raise ValueError("'initial_system_state' must be a dictionary")

                # For reading conditions
                self._parse_conditions(
                    conditions_dict=self.experiment.get("start_readings_conditions", {}),
                    store_dict_name="reading_condition_dict",
                    description="reading condition"
                )

                # For evaluation conditions
                self._parse_conditions(
                    conditions_dict=self.experiment.get("evaluation", {}),
                    store_dict_name="evaluation_equation_dic",
                    description="evaluation condition"
                )

                # System loop checks
                self.system_loop = self.experiment.get("system_loop", [])
                if not isinstance(self.system_loop, list):
                    raise ValueError("'system_loop' must be a list of connections")

            except KeyError as e:
                raise ValueError(f"Config missing required key: {e}")
            except TypeError as e:
                raise ValueError(f"Config has wrong type: {e}")

            
    ################################################################################
    ###########################   MAIN LOOP   ######################################
    ################################################################################
    async def main_experiment_loop(self):
        # initialize fmu servers, clients and vairable id storage
        
        # experiment_configs = [experiments/experiment1.yaml experiments/experiment2.yaml, ...]
        experiment_files = []
        for config in self.experiment_configs:
            experiment_files.append(os.path.join(config))


        #experiment_files = self.experiment_files
        #experiment_files = [os.path.join(self.experiment_files, i) for i in os.listdir(self.experiment_files)]

        for experiment_file in experiment_files:
            await self.initialize_experiment_params(experiment= experiment_file)
            self.server_obj = await server_manager.create(experiment_config= self.config)
            self.gather_system_ids()
            self.client_obj = await client_manager.create(system_servers = self.server_obj.system_servers, 
                                                          remote_servers = self.server_obj.remote_servers, 
                                                          system_node_ids= self.system_node_ids)
            
            await self.run_experiment()
            await self.server_obj.close()
            await self.client_obj.close()
