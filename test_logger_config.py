import main
import logging

print("ORBBot Logger Level:", main.logger.level)
print("ORBBot Logger Handlers:", main.logger.handlers)
print("Root Logger Handlers:", logging.getLogger().handlers)
print("Root Logger Level:", logging.getLogger().level)
