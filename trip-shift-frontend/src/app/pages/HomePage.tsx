import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import Panel from '../components/ui/Panel';
import { useAuth } from '../auth/AuthContext';

export default function HomePage() {
  const { t } = useTranslation();
  const { token } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="space-y-4">
      {!token && (
        <Panel>
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-medium mb-1">{t('home.welcomeGuestTitle', 'Welcome')}</h2>
              <p className="text-sm text-gray-700">{t('home.welcomeGuestMsg', 'Please login or register to start planning shifts.')}</p>
            </div>
            <div className="flex gap-2">
              <button className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}} onClick={() => navigate('/login')}>{t('auth.login')}</button>
              <button className="px-3 py-2 rounded-lg border text-sm hover:bg-neutral-50" onClick={() => navigate('/login?register=1')}>{t('auth.register')}</button>
            </div>
          </div>
        </Panel>
      )}
      <Panel>
        <h2 className="text-xl font-semibold mb-2">{t('home.title')}</h2>
        <p className="text-sm text-gray-700 mb-3">{t('home.intro')}</p>

        <h3 className="text-base font-medium mb-1">{t('home.getStartedTitle')}</h3>
        <ol className="list-decimal pl-5 space-y-1 text-sm text-gray-800">
          <li>{t('home.steps.login')} <Link className="text-blue-700 underline" to="/login">{t('home.links.login')}</Link></li>
          <li>{t('home.steps.createShift')} <Link className="text-blue-700 underline" to="/shifts">{t('home.links.shifts')}</Link></li>
          <li>{t('home.steps.pickDayRoute')}</li>
          <li>{t('home.steps.leaveDepot')}</li>
          <li>{t('home.steps.selectTrips')}</li>
          <li>{t('home.steps.returnDepot')}</li>
          <li>{t('home.steps.saveShift')}</li>
        </ol>
      </Panel>

      <Panel>
        <h3 className="text-base font-medium mb-1">{t('home.manageDataTitle')}</h3>
        <p className="text-sm text-gray-700 mb-2">{t('home.manageDataIntro')}</p>
        <ul className="list-disc pl-5 text-sm text-gray-800 space-y-1">
          <li><Link className="text-blue-700 underline" to="/depots">{t('home.links.depots')}</Link> — {t('home.desc.depots')}</li>
          <li><Link className="text-blue-700 underline" to="/fleet/models">{t('home.links.busModels')}</Link> — {t('home.desc.busModels')}</li>
          <li><Link className="text-blue-700 underline" to="/fleet/buses">{t('home.links.buses')}</Link> — {t('home.desc.buses')}</li>
        </ul>
      </Panel>

      <Panel>
        <h3 className="text-base font-medium mb-1">{t('home.tipsTitle')}</h3>
        <ul className="list-disc pl-5 text-sm text-gray-800 space-y-1">
          <li>{t('home.tips.language')}</li>
          <li>{t('home.tips.gtfsTime')}</li>
        </ul>
      </Panel>
    </div>
  );
}


