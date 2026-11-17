import { hrmsAPI } from '../../apiEntities.js';
import HrmsSelect from './HrmsSelect';

// Project dropdown — options ([{ id, name }]) come from the HRMS.
const fetchProjects = () => hrmsAPI.projects();

export default function HrmsProjectSelect(props) {
  return (
    <HrmsSelect
      {...props} cacheKey="projects" fetchOptions={fetchProjects}
      placeholder="Select Project" noun="projects"
    />
  );
}
