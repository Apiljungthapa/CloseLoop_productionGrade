import { hrmsAPI } from '../../apiEntities.js';
import HrmsSelect from './HrmsSelect';

// Activity dropdown — options come from the HRMS. onChange receives the whole
// activity, including its `sub_activities` ([{ id, name, key }]).
const fetchActivities = () => hrmsAPI.activities();

export default function HrmsActivitySelect(props) {
  return (
    <HrmsSelect
      {...props} cacheKey="activities" fetchOptions={fetchActivities}
      placeholder="Select Activity" noun="activities"
    />
  );
}
