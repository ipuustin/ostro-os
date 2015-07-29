'''Verify get sensor data based on its id'''
import os
from oeqa.utils.helper import get_files_dir
from oeqa.oetest import oeRuntimeTest
import readConfigFile

class TestSetSensorDataById(oeRuntimeTest):
    '''Verify sensor data can be returned based on sensor id'''
    def testSetSensorDataById(self):
        '''push binary to target and run it with argument'''
        #Prepare test binaries to image
        mkdir_path = "mkdir -p /opt/sensor-test/apps/"
        (status, output) = self.target.run(mkdir_path)
        copy_to_path = os.path.join(get_files_dir(), 'test_set_sensor_data_by_id')
        (status, output) = self.target.copy_to(copy_to_path, "/opt/sensor-test/apps/")
        #run test set sensor data by id and show it's information
        client_cmd = "/opt/sensor-test/apps/test_set_sensor_data_by_id " \
                     + readConfigFile.ReadConfFile.getSectionValue( 'sensors','valid-id')
        (status, output) = self.target.run(client_cmd)
        print output
        self.assertEqual(status, 1, msg="Error messages: %s" % output)
